#include <cassert>
#include <cmath>
#include <cstdio>

#include "order_book.hpp"

using namespace lob;

static bool close(double a, double b, double eps = 1e-9) {
    return std::fabs(a - b) < eps;
}

static void test_empty_book_has_no_top() {
    OrderBook book;
    auto top = book.top_of_book();
    assert(!top.has_bid);
    assert(!top.has_ask);
    assert(book.imbalance(5) == 0.0);
}

static void test_snapshot_then_top_of_book() {
    OrderBook book;
    book.apply_snapshot_level(Side::Bid, 100.0, 2.0);
    book.apply_snapshot_level(Side::Bid, 99.5, 5.0);
    book.apply_snapshot_level(Side::Ask, 100.5, 3.0);
    book.apply_snapshot_level(Side::Ask, 101.0, 1.0);

    auto top = book.top_of_book();
    assert(top.has_bid && top.has_ask);
    assert(close(top.best_bid, 100.0));
    assert(close(top.best_bid_size, 2.0));
    assert(close(top.best_ask, 100.5));
    assert(close(top.best_ask_size, 3.0));
}

static void test_update_changes_best_bid() {
    OrderBook book;
    book.apply_snapshot_level(Side::Bid, 100.0, 2.0);
    book.apply_snapshot_level(Side::Bid, 99.5, 5.0);

    // A new, better bid arrives.
    book.apply_update(BookUpdate{Side::Bid, 100.25, 1.5, 1});
    auto top = book.top_of_book();
    assert(close(top.best_bid, 100.25));
    assert(close(top.best_bid_size, 1.5));
    assert(book.bid_levels() == 3);
}

static void test_update_with_zero_size_removes_level() {
    OrderBook book;
    book.apply_snapshot_level(Side::Bid, 100.0, 2.0);
    book.apply_snapshot_level(Side::Bid, 99.5, 5.0);

    book.apply_update(BookUpdate{Side::Bid, 100.0, 0.0, 1});
    auto top = book.top_of_book();
    assert(close(top.best_bid, 99.5));
    assert(book.bid_levels() == 1);
}

static void test_snapshot_ignores_zero_size_levels() {
    OrderBook book;
    book.apply_snapshot_level(Side::Ask, 101.0, 0.0);
    assert(book.ask_levels() == 0);
}

static void test_imbalance_direction_and_bounds() {
    OrderBook book;
    book.apply_snapshot_level(Side::Bid, 100.0, 8.0);
    book.apply_snapshot_level(Side::Ask, 100.5, 2.0);

    // Heavier bid depth -> positive imbalance, bounded in (-1, 1].
    double imb = book.imbalance(5);
    assert(imb > 0.0 && imb <= 1.0);
    assert(close(imb, (8.0 - 2.0) / (8.0 + 2.0)));
}

static void test_imbalance_respects_depth_cutoff() {
    OrderBook book;
    book.apply_snapshot_level(Side::Bid, 100.0, 1.0);
    book.apply_snapshot_level(Side::Bid, 99.0, 100.0);   // outside depth=1
    book.apply_snapshot_level(Side::Ask, 100.5, 1.0);

    // depth=1 should only see the top level on each side -> balanced.
    assert(close(book.imbalance(1), 0.0));
}

int main() {
    test_empty_book_has_no_top();
    test_snapshot_then_top_of_book();
    test_update_changes_best_bid();
    test_update_with_zero_size_removes_level();
    test_snapshot_ignores_zero_size_levels();
    test_imbalance_direction_and_bounds();
    test_imbalance_respects_depth_cutoff();
    std::printf("All order_book tests passed.\n");
    return 0;
}
