#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace lob {

enum class Side { Bid, Ask };

struct Level {
    double price;
    double size;
};

struct BookUpdate {
    Side side;
    double price;
    double size;      // 0 means "remove this price level"
    uint64_t ts_ns;
};

struct TopOfBook {
    bool has_bid = false;
    bool has_ask = false;
    double best_bid = 0.0;
    double best_bid_size = 0.0;
    double best_ask = 0.0;
    double best_ask_size = 0.0;
};

// Reconstructs an L2 order book from a snapshot + a stream of incremental
// updates. Bids are kept highest-price-first, asks lowest-price-first, so
// top-of-book reads are O(1) via begin().
class OrderBook {
public:
    void apply_snapshot_level(Side side, double price, double size);
    void apply_update(const BookUpdate& update);

    TopOfBook top_of_book() const;

    // Sum of size across the best `depth` levels on each side.
    double bid_depth(std::size_t depth) const;
    double ask_depth(std::size_t depth) const;

    // (bid_depth - ask_depth) / (bid_depth + ask_depth) over the best
    // `depth` levels; 0.0 when both sides are empty. Positive = buy pressure.
    double imbalance(std::size_t depth) const;

    std::size_t bid_levels() const { return bids_.size(); }
    std::size_t ask_levels() const { return asks_.size(); }

private:
    // std::greater orders bids descending (best bid first).
    std::map<double, double, std::greater<double>> bids_;
    std::map<double, double> asks_;
};

}  // namespace lob
