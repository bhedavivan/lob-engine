// Replays a captured L2 order book feed (see data/README.md for the CSV
// contract) through OrderBook and prints live top-of-book / imbalance.
//
// Usage: lob_engine <path-to-csv> [--depth N] [--every N]

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
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
};

Args parse_args(int argc, char** argv) {
    Args args;
    if (argc < 2) {
        std::fprintf(stderr, "usage: %s <path-to-csv> [--depth N] [--every N]\n", argv[0]);
        std::exit(1);
    }
    args.path = argv[1];
    for (int i = 2; i < argc; ++i) {
        if (std::strcmp(argv[i], "--depth") == 0 && i + 1 < argc) {
            args.depth = static_cast<std::size_t>(std::atoi(argv[++i]));
        } else if (std::strcmp(argv[i], "--every") == 0 && i + 1 < argc) {
            args.print_every = static_cast<std::size_t>(std::atoi(argv[++i]));
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

    OrderBook book;
    std::string line;
    // Skip the header row.
    std::getline(in, line);

    std::size_t rows = 0;
    std::size_t snapshot_rows = 0;
    std::size_t update_rows = 0;
    std::size_t malformed_rows = 0;

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

        if (type == "snapshot") {
            book.apply_snapshot_level(side, price, size);
            ++snapshot_rows;
        } else if (type == "update") {
            book.apply_update(BookUpdate{side, price, size, ts_ns});
            ++update_rows;
        } else {
            ++malformed_rows;
            continue;
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
    return 0;
}
