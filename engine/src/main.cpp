// Replays a captured L2 order book feed (see data/README.md for the CSV
// contract) through OrderBook and prints live top-of-book / imbalance.
// With --emit, also writes a per-event microstructure feature stream that
// the Python backtester (backtest/) consumes.
//
// Usage: lob_engine <path-to-csv> [--depth N] [--every N] [--emit <out.csv>]

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

#include "order_book.hpp"

using namespace lob;

namespace {

struct Args {
    std::string path;
    std::size_t depth = 5;
    std::size_t print_every = 500;
    std::string emit_path;  // empty = no feature dump
};

Args parse_args(int argc, char** argv) {
    Args args;
    if (argc < 2) {
        std::fprintf(stderr,
                     "usage: %s <path-to-csv> [--depth N] [--every N] [--emit <out.csv>]\n",
                     argv[0]);
        std::exit(1);
    }
    args.path = argv[1];
    for (int i = 2; i < argc; ++i) {
        if (std::strcmp(argv[i], "--depth") == 0 && i + 1 < argc) {
            args.depth = static_cast<std::size_t>(std::atoi(argv[++i]));
        } else if (std::strcmp(argv[i], "--every") == 0 && i + 1 < argc) {
            args.print_every = static_cast<std::size_t>(std::atoi(argv[++i]));
        } else if (std::strcmp(argv[i], "--emit") == 0 && i + 1 < argc) {
            args.emit_path = argv[++i];
        }
    }
    return args;
}

std::vector<std::string> split_csv_line(const std::string& line) {
    std::vector<std::string> fields;
    std::stringstream ss(line);
    std::string field;
    while (std::getline(ss, field, ',')) {
        fields.push_back(field);
    }
    return fields;
}

}  // namespace

int main(int argc, char** argv) {
    Args args = parse_args(argc, argv);

    std::ifstream in(args.path);
    if (!in.is_open()) {
        std::fprintf(stderr, "could not open '%s'\n", args.path.c_str());
        return 1;
    }

    // Optional per-event feature stream for the backtester.
    std::ofstream emit;
    if (!args.emit_path.empty()) {
        emit.open(args.emit_path);
        if (!emit.is_open()) {
            std::fprintf(stderr, "could not open emit path '%s'\n", args.emit_path.c_str());
            return 1;
        }
        // 12 significant figures so cent-level prices and 8-dp crypto sizes
        // both survive the round-trip (default stream precision is 6, which
        // would silently truncate a ~65000 BTC price to the dollar).
        emit << std::setprecision(12);
        emit << "event_idx,ts_ns,best_bid,best_ask,bid_size,ask_size,mid,microprice,spread,"
                "imb1,imb5,imb10\n";
    }

    OrderBook book;
    std::string line;
    // Skip the header row.
    std::getline(in, line);

    std::size_t rows = 0;
    std::size_t snapshot_rows = 0;
    std::size_t update_rows = 0;
    std::size_t malformed_rows = 0;
    std::size_t emitted_rows = 0;

    auto start = std::chrono::steady_clock::now();

    while (std::getline(in, line)) {
        if (line.empty()) continue;
        auto fields = split_csv_line(line);
        if (fields.size() < 4) {
            ++malformed_rows;
            continue;
        }

        const std::string& type = fields[0];
        Side side = (fields[1] == "b") ? Side::Bid : Side::Ask;
        double price = std::atof(fields[2].c_str());
        double size = std::atof(fields[3].c_str());
        uint64_t ts_ns = (fields.size() > 4) ? std::strtoull(fields[4].c_str(), nullptr, 10) : 0;

        bool is_update = false;
        if (type == "snapshot") {
            book.apply_snapshot_level(side, price, size);
            ++snapshot_rows;
        } else if (type == "update") {
            book.apply_update(BookUpdate{side, price, size, ts_ns});
            ++update_rows;
            is_update = true;
        } else {
            ++malformed_rows;
            continue;
        }

        // Emit one feature row per update event, once the book is two-sided.
        // Snapshot rows only build the initial book, so they're skipped.
        if (emit.is_open() && is_update) {
            auto top = book.top_of_book();
            if (top.has_bid && top.has_ask) {
                double mid = 0.5 * (top.best_bid + top.best_ask);
                emit << emitted_rows << ',' << ts_ns << ',' << top.best_bid << ','
                     << top.best_ask << ',' << top.best_bid_size << ',' << top.best_ask_size
                     << ',' << mid << ',' << book.microprice() << ','
                     << (top.best_ask - top.best_bid) << ',' << book.imbalance(1) << ','
                     << book.imbalance(5) << ',' << book.imbalance(10) << '\n';
                ++emitted_rows;
            }
        }

        ++rows;
        if (args.print_every > 0 && rows % args.print_every == 0) {
            auto top = book.top_of_book();
            std::printf(
                "[row %zu] bid=%.2f (%.4f) ask=%.2f (%.4f) spread=%.2f imbalance(%zu)=%.4f "
                "levels=%zu/%zu\n",
                rows, top.best_bid, top.best_bid_size, top.best_ask, top.best_ask_size,
                top.has_bid && top.has_ask ? top.best_ask - top.best_bid : 0.0, args.depth,
                book.imbalance(args.depth), book.bid_levels(), book.ask_levels());
        }
    }

    auto end = std::chrono::steady_clock::now();
    double elapsed_s = std::chrono::duration<double>(end - start).count();

    auto top = book.top_of_book();
    std::printf("\n--- replay complete ---\n");
    std::printf("rows: %zu (snapshot=%zu, update=%zu, malformed=%zu)\n", rows, snapshot_rows,
                update_rows, malformed_rows);
    std::printf("elapsed: %.4fs (%.0f rows/sec)\n", elapsed_s,
                elapsed_s > 0 ? rows / elapsed_s : 0.0);
    std::printf("final top-of-book: bid=%.2f ask=%.2f imbalance(%zu)=%.4f\n", top.best_bid,
                top.best_ask, args.depth, book.imbalance(args.depth));
    if (emit.is_open()) {
        std::printf("emitted %zu feature rows to %s\n", emitted_rows, args.emit_path.c_str());
    }
    return 0;
}
