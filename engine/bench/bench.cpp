// Microbenchmark for the replay hot path. It answers one question before any
// optimization is attempted: where does the time actually go -- parsing the
// CSV, or maintaining the order book?
//
// It (1) times the old stringstream+atof parser against the from_chars
// fast_parse, verifying they agree, and (2) isolates order-book update latency
// by replaying pre-parsed events from memory. Everything runs from an
// in-memory copy of the file so disk I/O never pollutes the numbers.
//
// Usage: bench <path-to-csv> [--repeats N]

#include <charconv>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "fast_order_book.hpp"
#include "order_book.hpp"
#include "parse.hpp"

using namespace lob;
using Clock = std::chrono::steady_clock;

namespace {

// The original parser, kept here only as the benchmark baseline.
struct SlowRow {
    std::string type;
    char side;
    double price;
    double size;
    uint64_t ts_ns;
};

bool slow_parse(const std::string& line, SlowRow& out) {
    std::vector<std::string> fields;
    std::stringstream ss(line);
    std::string field;
    while (std::getline(ss, field, ',')) fields.push_back(field);
    if (fields.size() < 4) return false;
    out.type = fields[0];
    out.side = fields[1].empty() ? '\0' : fields[1][0];
    out.price = std::atof(fields[2].c_str());
    out.size = std::atof(fields[3].c_str());
    out.ts_ns = (fields.size() > 4) ? std::strtoull(fields[4].c_str(), nullptr, 10) : 0;
    return true;
}

double ns_per(std::chrono::nanoseconds total, std::size_t count) {
    return count ? static_cast<double>(total.count()) / count : 0.0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: %s <path-to-csv> [--repeats N]\n", argv[0]);
        return 1;
    }
    int repeats = 5;
    for (int i = 2; i < argc; ++i) {
        if (std::strcmp(argv[i], "--repeats") == 0 && i + 1 < argc) repeats = std::atoi(argv[++i]);
    }

    // Load the whole file into memory; the header is dropped.
    std::ifstream in(argv[1]);
    if (!in.is_open()) {
        std::fprintf(stderr, "could not open '%s'\n", argv[1]);
        return 1;
    }
    std::vector<std::string> lines;
    std::string line;
    std::getline(in, line);  // header
    while (std::getline(in, line)) {
        if (!line.empty()) lines.push_back(line);
    }
    std::printf("loaded %zu rows into memory, %d repeats\n\n", lines.size(), repeats);

    // --- Correctness: the fast parser must agree with the baseline ---
    std::size_t mismatches = 0;
    for (const auto& l : lines) {
        SlowRow s;
        ParsedRow f;
        bool so = slow_parse(l, s);
        bool fo = fast_parse(l, f);
        if (so != fo) { ++mismatches; continue; }
        if (!so) continue;
        if (s.type != f.type || s.side != f.side || s.price != f.price ||
            s.size != f.size || s.ts_ns != f.ts_ns) {
            ++mismatches;
        }
    }
    std::printf("parser agreement: %s (%zu mismatches)\n\n",
                mismatches == 0 ? "OK" : "FAILED", mismatches);

    // --- Benchmark 1: parsing ---
    volatile double sink = 0.0;  // defeat dead-code elimination
    auto t0 = Clock::now();
    for (int r = 0; r < repeats; ++r) {
        for (const auto& l : lines) {
            SlowRow s;
            if (slow_parse(l, s)) sink += s.price + s.size;
        }
    }
    auto slow_t = Clock::now() - t0;

    t0 = Clock::now();
    for (int r = 0; r < repeats; ++r) {
        for (const auto& l : lines) {
            ParsedRow f;
            if (fast_parse(l, f)) sink += f.price + f.size;
        }
    }
    auto fast_t = Clock::now() - t0;

    std::size_t parsed = lines.size() * repeats;
    double slow_ns = ns_per(std::chrono::duration_cast<std::chrono::nanoseconds>(slow_t), parsed);
    double fast_ns = ns_per(std::chrono::duration_cast<std::chrono::nanoseconds>(fast_t), parsed);
    std::printf("=== Parsing (per row) ===\n");
    std::printf("  stringstream+atof : %7.1f ns/row  (%6.2f M rows/s)\n", slow_ns, 1e3 / slow_ns);
    std::printf("  from_chars        : %7.1f ns/row  (%6.2f M rows/s)\n", fast_ns, 1e3 / fast_ns);
    std::printf("  speedup           : %5.1fx\n\n", slow_ns / fast_ns);

    // --- Benchmark 2: isolated order-book update latency ---
    // Pre-parse every book event so the timer sees only apply_* calls.
    struct Ev { bool snapshot; Side side; double price; double size; };
    std::vector<Ev> evs;
    evs.reserve(lines.size());
    for (const auto& l : lines) {
        ParsedRow f;
        if (!fast_parse(l, f)) continue;
        if (f.type == "snapshot")
            evs.push_back({true, f.side == 'b' ? Side::Bid : Side::Ask, f.price, f.size});
        else if (f.type == "update")
            evs.push_back({false, f.side == 'b' ? Side::Bid : Side::Ask, f.price, f.size});
    }

    std::size_t updates = 0;
    t0 = Clock::now();
    for (int r = 0; r < repeats; ++r) {
        OrderBook book;
        for (const auto& e : evs) {
            if (e.snapshot) {
                book.apply_snapshot_level(e.side, e.price, e.size);
            } else {
                book.apply_update(BookUpdate{e.side, e.price, e.size, 0});
                ++updates;
            }
        }
        sink += book.imbalance(5);
    }
    auto map_t = Clock::now() - t0;

    std::size_t fast_updates = 0;
    t0 = Clock::now();
    for (int r = 0; r < repeats; ++r) {
        FastOrderBook book;
        for (const auto& e : evs) {
            if (e.snapshot) {
                book.apply_snapshot_level(e.side, e.price, e.size);
            } else {
                book.apply_update(BookUpdate{e.side, e.price, e.size, 0});
                ++fast_updates;
            }
        }
        sink += book.imbalance(5);
    }
    auto fast_book_t = Clock::now() - t0;

    double map_ns = ns_per(std::chrono::duration_cast<std::chrono::nanoseconds>(map_t), updates);
    double fastb_ns =
        ns_per(std::chrono::duration_cast<std::chrono::nanoseconds>(fast_book_t), fast_updates);
    std::printf("=== Order-book apply_update (isolated) ===\n");
    std::printf("  std::map (tree)   : %7.1f ns/update  (%6.2f M updates/s)\n", map_ns,
                1e3 / map_ns);
    std::printf("  flat array        : %7.1f ns/update  (%6.2f M updates/s)\n", fastb_ns,
                1e3 / fastb_ns);
    std::printf("  speedup           : %5.1fx\n\n", map_ns / fastb_ns);

    std::printf("takeaway: parsing was the bottleneck at %.1fx a tree book update;\n"
                "from_chars fixed that, then the flat array attacks the update itself.\n",
                slow_ns / map_ns);
    (void)sink;
    return 0;
}
