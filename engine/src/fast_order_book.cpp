#include "fast_order_book.hpp"

#include <cmath>

namespace lob {

FastOrderBook::FastOrderBook(double tick)
    : inv_tick_(std::llround(1.0 / tick)),  // 100 for a 0.01 tick
      bid_(static_cast<std::size_t>(W), 0.0),
      ask_(static_cast<std::size_t>(W), 0.0) {}

long FastOrderBook::tick_index(double price) const {
    return std::llround(price * inv_tick_);
}

double FastOrderBook::price_at(long ti) const {
    // Integer-grid division reproduces the exact double the feed carried,
    // because feed prices are already multiples of the tick.
    return static_cast<double>(ti) / inv_tick_;
}

void FastOrderBook::apply_snapshot_level(Side side, double price, double size) {
    if (size <= 0.0) return;
    set_level(side, price, size);
}

void FastOrderBook::apply_update(const BookUpdate& update) {
    set_level(update.side, update.price, update.size);
}

void FastOrderBook::set_level(Side side, double price, double size) {
    if (!based_) {
        base_ = tick_index(price) - W / 2;
        based_ = true;
    }
    long i = tick_index(price) - base_;
    bool in_window = (i >= 0 && i < W);

    if (side == Side::Bid) {
        if (!in_window) {
            if (size > 0.0) bid_far_[price] = size;
            else bid_far_.erase(price);
            return;
        }
        bid_[static_cast<std::size_t>(i)] = (size > 0.0) ? size : 0.0;
        if (size > 0.0) {
            if (i > best_bid_i_) best_bid_i_ = i;
        } else if (i == best_bid_i_) {
            while (best_bid_i_ >= 0 && bid_[static_cast<std::size_t>(best_bid_i_)] == 0.0)
                --best_bid_i_;
        }
    } else {
        if (!in_window) {
            if (size > 0.0) ask_far_[price] = size;
            else ask_far_.erase(price);
            return;
        }
        ask_[static_cast<std::size_t>(i)] = (size > 0.0) ? size : 0.0;
        if (size > 0.0) {
            if (best_ask_i_ < 0 || i < best_ask_i_) best_ask_i_ = i;
        } else if (i == best_ask_i_) {
            while (best_ask_i_ < W && ask_[static_cast<std::size_t>(best_ask_i_)] == 0.0)
                ++best_ask_i_;
            if (best_ask_i_ >= W) best_ask_i_ = -1;
        }
    }
}

TopOfBook FastOrderBook::top_of_book() const {
    TopOfBook top;
    if (best_bid_i_ >= 0) {
        top.has_bid = true;
        top.best_bid = price_at(base_ + best_bid_i_);
        top.best_bid_size = bid_[static_cast<std::size_t>(best_bid_i_)];
    } else if (!bid_far_.empty()) {
        top.has_bid = true;
        top.best_bid = bid_far_.begin()->first;
        top.best_bid_size = bid_far_.begin()->second;
    }
    if (best_ask_i_ >= 0) {
        top.has_ask = true;
        top.best_ask = price_at(base_ + best_ask_i_);
        top.best_ask_size = ask_[static_cast<std::size_t>(best_ask_i_)];
    } else if (!ask_far_.empty()) {
        top.has_ask = true;
        top.best_ask = ask_far_.begin()->first;
        top.best_ask_size = ask_far_.begin()->second;
    }
    return top;
}

double FastOrderBook::bid_depth(std::size_t depth) const {
    double total = 0.0;
    std::size_t n = 0;
    for (long i = best_bid_i_; i >= 0 && n < depth; --i) {
        if (bid_[static_cast<std::size_t>(i)] > 0.0) {
            total += bid_[static_cast<std::size_t>(i)];
            ++n;
        }
    }
    for (auto it = bid_far_.begin(); it != bid_far_.end() && n < depth; ++it, ++n) {
        total += it->second;
    }
    return total;
}

double FastOrderBook::ask_depth(std::size_t depth) const {
    double total = 0.0;
    std::size_t n = 0;
    for (long i = best_ask_i_; i >= 0 && i < W && n < depth; ++i) {
        if (ask_[static_cast<std::size_t>(i)] > 0.0) {
            total += ask_[static_cast<std::size_t>(i)];
            ++n;
        }
    }
    for (auto it = ask_far_.begin(); it != ask_far_.end() && n < depth; ++it, ++n) {
        total += it->second;
    }
    return total;
}

double FastOrderBook::imbalance(std::size_t depth) const {
    double bd = bid_depth(depth);
    double ad = ask_depth(depth);
    double denom = bd + ad;
    if (denom <= 0.0) return 0.0;
    return (bd - ad) / denom;
}

double FastOrderBook::microprice() const {
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
