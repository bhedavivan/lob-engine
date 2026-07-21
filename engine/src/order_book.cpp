#include "order_book.hpp"

namespace lob {

void OrderBook::apply_snapshot_level(Side side, double price, double size) {
    if (size <= 0.0) return;
    if (side == Side::Bid) {
        bids_[price] = size;
    } else {
        asks_[price] = size;
    }
}

void OrderBook::apply_update(const BookUpdate& update) {
    if (update.side == Side::Bid) {
        if (update.size <= 0.0) {
            bids_.erase(update.price);
        } else {
            bids_[update.price] = update.size;
        }
    } else {
        if (update.size <= 0.0) {
            asks_.erase(update.price);
        } else {
            asks_[update.price] = update.size;
        }
    }
}

TopOfBook OrderBook::top_of_book() const {
    TopOfBook top;
    if (!bids_.empty()) {
        top.has_bid = true;
        top.best_bid = bids_.begin()->first;
        top.best_bid_size = bids_.begin()->second;
    }
    if (!asks_.empty()) {
        top.has_ask = true;
        top.best_ask = asks_.begin()->first;
        top.best_ask_size = asks_.begin()->second;
    }
    return top;
}

double OrderBook::bid_depth(std::size_t depth) const {
    double total = 0.0;
    std::size_t n = 0;
    for (auto it = bids_.begin(); it != bids_.end() && n < depth; ++it, ++n) {
        total += it->second;
    }
    return total;
}

double OrderBook::ask_depth(std::size_t depth) const {
    double total = 0.0;
    std::size_t n = 0;
    for (auto it = asks_.begin(); it != asks_.end() && n < depth; ++it, ++n) {
        total += it->second;
    }
    return total;
}

double OrderBook::imbalance(std::size_t depth) const {
    double bd = bid_depth(depth);
    double ad = ask_depth(depth);
    double denom = bd + ad;
    if (denom <= 0.0) return 0.0;
    return (bd - ad) / denom;
}

double OrderBook::microprice() const {
    TopOfBook top = top_of_book();
    if (!top.has_bid && !top.has_ask) return 0.0;
    if (!top.has_ask) return top.best_bid;
    if (!top.has_bid) return top.best_ask;

    double size_sum = top.best_bid_size + top.best_ask_size;
    double mid = 0.5 * (top.best_bid + top.best_ask);
    if (size_sum <= 0.0) return mid;
    return (top.best_bid * top.best_ask_size + top.best_ask * top.best_bid_size) / size_sum;
}

}  // namespace lob
