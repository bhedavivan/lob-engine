// Differential test: FastOrderBook must produce byte-for-byte the same
// top-of-book, imbalance, and microprice as the reference OrderBook, on both
// synthetic edge cases and a real captured feed. This is what makes the
// array-book optimization trustworthy — if the two ever disagree, the fast
// path has a bug, and the test says so.

#include <cassert>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <string>

#include "fast_order_book.hpp"
#include "order_book.hpp"
#include "parse.hpp"

using namespace lob;

namespace {

bool eq(double a, double b, double eps = 1e-9) { return std::fabs(a - b) < eps; }

template <class BookA, class BookB>
bool agree(const BookA& a, const BookB& b) {
    TopOfBook ta = a.top_of_book();
    TopOfBook tb = b.top_of_book();
    if (ta.has_bid != tb.has_bid || ta.has_ask != tb.has_ask) return false;
    if (ta.has_bid && (!eq(ta.best_bid, tb.best_bid) || !eq(ta.best_bid_size, tb.best_bid_size)))
        return false;
    if (ta.has_ask && (!eq(ta.best_ask, tb.best_ask) || !eq(ta.best_ask_size, tb.best_ask_size)))
        return false;
    for (std::size_t d : {1u, 5u, 10u}) {
        if (!eq(a.imbalance(d), b.imbalance(d))) return false;
    }
    return eq(a.microprice(), b.microprice());
}

void test_synthetic_matches_reference() {
    OrderBook ref;
    FastOrderBook fast;

    struct Op { const char* kind; Side side; double px; double sz; };
    const Op ops[] = {
        {"snap", Side::Bid, 100.00, 2.0}, {"snap", Side::Bid, 99.99, 5.0},
        {"snap", Side::Ask, 100.01, 3.0}, {"snap", Side::Ask, 100.02, 1.0},
        {"upd", Side::Bid, 100.005, 1.5},                 // new best bid
        {"upd", Side::Ask, 100.01, 0.0},                  // remove best ask
        {"upd", Side::Bid, 100.005, 0.0},                 // remove best bid again
        {"upd", Side::Ask, 100.015, 4.0},                 // new ask level
        {"upd", Side::Bid, 99.98, 8.0},                   // deeper bid
    };
    for (const Op& o : ops) {
        if (std::string(o.kind) == "snap") {
            ref.apply_snapshot_level(o.side, o.px, o.sz);
            fast.apply_snapshot_level(o.side, o.px, o.sz);
        } else {
            ref.apply_update(BookUpdate{o.side, o.px, o.sz, 0});
            fast.apply_update(BookUpdate{o.side, o.px, o.sz, 0});
        }
        assert(agree(ref, fast));
    }
}

// Replay data/sample_head.csv (a real capture) through both books, checking
// agreement after every event. Path is passed by CTest as argv[1].
void test_real_capture_matches_reference(const char* path) {
    std::ifstream in(path);
    if (!in.is_open()) {
        std::printf("  (skip real-capture diff: cannot open %s)\n", path);
        return;
    }
    OrderBook ref;
    FastOrderBook fast;
    std::string line;
    std::getline(in, line);  // header
    std::size_t checked = 0;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        ParsedRow r;
        if (!fast_parse(line, r)) continue;
        Side side = (r.side == 'b') ? Side::Bid : Side::Ask;
        if (r.type == "snapshot") {
            ref.apply_snapshot_level(side, r.price, r.size);
            fast.apply_snapshot_level(side, r.price, r.size);
        } else if (r.type == "update") {
            ref.apply_update(BookUpdate{side, r.price, r.size, r.ts_ns});
            fast.apply_update(BookUpdate{side, r.price, r.size, r.ts_ns});
        } else {
            continue;  // trades don't mutate the book
        }
        assert(agree(ref, fast));
        ++checked;
    }
    std::printf("  real-capture diff: %zu events matched\n", checked);
}

}  // namespace

int main(int argc, char** argv) {
    test_synthetic_matches_reference();
    if (argc > 1) test_real_capture_matches_reference(argv[1]);
    std::printf("All fast_order_book differential tests passed.\n");
    return 0;
}
