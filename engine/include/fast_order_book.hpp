#pragma once

#include <cstddef>
#include <map>
#include <vector>

#include "order_book.hpp"

namespace lob {

// A drop-in alternative to OrderBook that keeps the dense region around the
// touch in flat, tick-indexed arrays (O(1) update, contiguous memory) instead
// of a red-black tree. Deep levels that fall outside the array window spill
// into std::map fallbacks, so the book stays complete; those maps are never
// touched by top-of-book or small-depth imbalance, which always live near the
// touch and therefore in the arrays.
//
// Prices are reconstructed from the integer tick grid as `ti / inv_tick`, which
// returns the *same* double the feed carried (feed prices already sit on the
// tick grid), so results match OrderBook exactly — see the differential test.
//
// The window is centered on the first price seen and does not re-base; a price
// that moves beyond +/- (W/2) ticks spills to the map. W is sized so that never
// happens over a normal session (a ~$1300 band for BTC at a 1-cent tick).
class FastOrderBook {
public:
    explicit FastOrderBook(double tick = 0.01);

    void apply_snapshot_level(Side side, double price, double size);
    void apply_update(const BookUpdate& update);

    TopOfBook top_of_book() const;
    double bid_depth(std::size_t depth) const;
    double ask_depth(std::size_t depth) const;
    double imbalance(std::size_t depth) const;
    double microprice() const;

private:
    static constexpr long W = 1L << 17;  // window width in ticks

    void set_level(Side side, double price, double size);
    long tick_index(double price) const;   // llround(price * inv_tick_)
    double price_at(long ti) const;        // ti / inv_tick_ (exact on-grid)

    double inv_tick_;
    bool based_ = false;
    long base_ = 0;                        // tick index of array slot 0

    std::vector<double> bid_;              // size resting at each in-window tick
    std::vector<double> ask_;
    long best_bid_i_ = -1;                 // highest occupied bid slot, -1 = none
    long best_ask_i_ = -1;                 // lowest occupied ask slot, -1 = none

    std::map<double, double, std::greater<double>> bid_far_;  // out-of-window
    std::map<double, double> ask_far_;
};

}  // namespace lob
