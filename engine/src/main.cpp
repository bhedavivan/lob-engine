// Replays a captured L2 order book feed (see data/README.md for the CSV
// contract) through OrderBook and prints live top-of-book / imbalance.
// A path of "-" reads the feed live from stdin instead of a file.
//
// Optional outputs, each written in one pass over the same replay:
//   --emit <csv>         per-event microstructure feature stream (backtest/)
//   --emit-events <csv>  unified quote+trade stream (market-making sim)
//   --emit-depth <csv>   periodic depth-ladder snapshots (dashboard/)
//
// Usage: lob_engine <path-to-csv|-> [--depth N] [--every N] [--emit <csv>]
//        [--emit-events <csv>] [--emit-depth <csv>] [--depth-levels N]
//        [--depth-every N]

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>

#include "order_book.hpp"
#include "parse.hpp"

using namespace lob;

namespace {

struct Args {
    std::string path;
    std::size_t depth = 5;
    std::size_t print_every = 500;
    std::string emit_path;         // empty = no per-event feature dump
    std::string emit_events_path;  // empty = no unified quote+trade stream
    std::string emit_depth_path;   // empty = no depth-ladder snapshots
    std::size_t depth_levels = 12;
    std::size_t depth_every = 1000;
};

[[noreturn]] void usage_and_exit(const char* prog, int code) {
    std::fprintf(stderr,
                 "usage: %s <path-to-csv|-> [--depth N] [--every N] [--emit <csv>]\n"
                 "       [--emit-events <csv>] [--emit-depth <csv>] [--depth-levels N]\n"
                 "       [--depth-every N]\n"
                 "  '-' as the path reads the feed live from stdin\n",
                 prog);
    std::exit(code);
}

Args parse_args(int argc, char** argv) {
    Args args;
    if (argc < 2) usage_and_exit(argv[0], 1);
    args.path = argv[1];

    // Consume the value that must follow a flag, or fail loudly rather than
    // silently ignoring a misused option.
    auto value_for = [&](int& i) -> const char* {
        if (i + 1 >= argc) {
            std::fprintf(stderr, "error: %s requires a value\n", argv[i]);
            usage_and_exit(argv[0], 1);
        }
        return argv[++i];
    };
    auto to_size = [](const char* s) {
        return static_cast<std::size_t>(std::strtoull(s, nullptr, 10));
    };

    for (int i = 2; i < argc; ++i) {
        std::string_view arg = argv[i];
        if (arg == "--depth") args.depth = to_size(value_for(i));
        else if (arg == "--every") args.print_every = to_size(value_for(i));
        else if (arg == "--emit") args.emit_path = value_for(i);
        else if (arg == "--emit-events") args.emit_events_path = value_for(i);
        else if (arg == "--emit-depth") args.emit_depth_path = value_for(i);
        else if (arg == "--depth-levels") args.depth_levels = to_size(value_for(i));
        else if (arg == "--depth-every") args.depth_every = to_size(value_for(i));
        else {
            std::fprintf(stderr, "error: unknown argument '%s'\n", argv[i]);
            usage_and_exit(argv[0], 1);
        }
    }
    return args;
}

}  // namespace

int main(int argc, char** argv) {
    Args args = parse_args(argc, argv);

    // A path of "-" reads the feed live from stdin, so the engine can sit at
    // the end of a pipe: `capture_feed.py --stream | lob_engine -`.
    std::ifstream fin;
    std::istream* in_ptr = &std::cin;
    if (args.path != "-") {
        fin.open(args.path);
        if (!fin.is_open()) {
            std::fprintf(stderr, "could not open '%s'\n", args.path.c_str());
            return 1;
        }
        in_ptr = &fin;
    }
    std::istream& in = *in_ptr;

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

    // Optional unified quote+trade event stream for the market-making sim.
    std::ofstream emit_events;
    if (!args.emit_events_path.empty()) {
        emit_events.open(args.emit_events_path);
        if (!emit_events.is_open()) {
            std::fprintf(stderr, "could not open emit-events path '%s'\n",
                         args.emit_events_path.c_str());
            return 1;
        }
        emit_events << std::setprecision(12);
        emit_events << "event_type,ts_ns,best_bid,best_ask,bid_size,ask_size,"
                       "trade_price,trade_size,trade_side\n";
    }

    // Optional periodic depth-ladder snapshots for the dashboard.
    std::ofstream emit_depth;
    std::size_t depth_snaps = 0;
    if (!args.emit_depth_path.empty()) {
        emit_depth.open(args.emit_depth_path);
        if (!emit_depth.is_open()) {
            std::fprintf(stderr, "could not open emit-depth path '%s'\n",
                         args.emit_depth_path.c_str());
            return 1;
        }
        emit_depth << std::setprecision(12);
        emit_depth << "snap,ts_ns,side,rank,price,size\n";
    }

    OrderBook book;
    std::string line;
    // Skip the header row.
    std::getline(in, line);

    std::size_t rows = 0;
    std::size_t snapshot_rows = 0;
    std::size_t update_rows = 0;
    std::size_t trade_rows = 0;
    std::size_t malformed_rows = 0;
    std::size_t emitted_rows = 0;
    std::size_t events_emitted = 0;

    auto start = std::chrono::steady_clock::now();

    while (std::getline(in, line)) {
        if (line.empty()) continue;
        ParsedRow row;
        if (!fast_parse(line, row)) {
            ++malformed_rows;
            continue;
        }

        const std::string_view& type = row.type;
        Side side = (row.side == 'b') ? Side::Bid : Side::Ask;
        double price = row.price;
        double size = row.size;
        uint64_t ts_ns = row.ts_ns;

        bool is_update = false;
        bool is_trade = false;
        if (type == "snapshot") {
            book.apply_snapshot_level(side, price, size);
            ++snapshot_rows;
        } else if (type == "update") {
            book.apply_update(BookUpdate{side, price, size, ts_ns});
            ++update_rows;
            is_update = true;
        } else if (type == "trade") {
            // A trade print does not mutate the book (the corresponding level
            // changes arrive as their own update rows); it's carried through to
            // the event stream so the MM sim can test its resting quotes.
            ++trade_rows;
            is_trade = true;
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

        // Unified quote+trade stream: a row per update (quote) and per trade,
        // both stamped with the prevailing two-sided top of book.
        if (emit_events.is_open() && (is_update || is_trade)) {
            auto top = book.top_of_book();
            if (top.has_bid && top.has_ask) {
                emit_events << (is_trade ? "trade" : "quote") << ',' << ts_ns << ','
                            << top.best_bid << ',' << top.best_ask << ',' << top.best_bid_size
                            << ',' << top.best_ask_size << ',';
                if (is_trade) {
                    emit_events << price << ',' << size << ',' << row.side;
                } else {
                    emit_events << "0,0,";  // no trade on a quote row
                }
                emit_events << '\n';
                ++events_emitted;
            }
        }

        // Periodic depth-ladder snapshot (dashboard input). The depth_every > 0
        // guard keeps a `--depth-every 0` from dividing by zero.
        if (emit_depth.is_open() && is_update && args.depth_every > 0 &&
            update_rows % args.depth_every == 0) {
            auto top = book.top_of_book();
            if (top.has_bid && top.has_ask) {
                auto bids = book.top_bids(args.depth_levels);
                auto asks = book.top_asks(args.depth_levels);
                for (std::size_t k = 0; k < bids.size(); ++k) {
                    emit_depth << depth_snaps << ',' << ts_ns << ",b," << k << ','
                               << bids[k].price << ',' << bids[k].size << '\n';
                }
                for (std::size_t k = 0; k < asks.size(); ++k) {
                    emit_depth << depth_snaps << ',' << ts_ns << ",a," << k << ','
                               << asks[k].price << ',' << asks[k].size << '\n';
                }
                ++depth_snaps;
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
    std::printf("rows: %zu (snapshot=%zu, update=%zu, trade=%zu, malformed=%zu)\n", rows,
                snapshot_rows, update_rows, trade_rows, malformed_rows);
    std::printf("elapsed: %.4fs (%.0f rows/sec)\n", elapsed_s,
                elapsed_s > 0 ? rows / elapsed_s : 0.0);
    std::printf("final top-of-book: bid=%.2f ask=%.2f imbalance(%zu)=%.4f\n", top.best_bid,
                top.best_ask, args.depth, book.imbalance(args.depth));
    if (emit.is_open()) {
        std::printf("emitted %zu feature rows to %s\n", emitted_rows, args.emit_path.c_str());
    }
    if (emit_events.is_open()) {
        std::printf("emitted %zu quote+trade events to %s\n", events_emitted,
                    args.emit_events_path.c_str());
    }
    if (emit_depth.is_open()) {
        std::printf("emitted %zu depth snapshots to %s\n", depth_snaps,
                    args.emit_depth_path.c_str());
    }
    return 0;
}
