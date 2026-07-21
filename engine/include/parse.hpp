#pragma once

#include <charconv>
#include <cstdint>
#include <string_view>

// Zero-allocation parser for the capture CSV row format:
//   type,side,price,size[,ts_ns]
// Fields are views into the caller's line buffer, which must outlive the
// result. Numbers go through std::from_chars (no locale, no allocation),
// which is the fast path this project's benchmark exists to justify.

namespace lob {

struct ParsedRow {
    std::string_view type;  // "snapshot" | "update" | "trade"
    char side = '\0';       // 'b' | 'a'
    double price = 0.0;
    double size = 0.0;
    uint64_t ts_ns = 0;
};

inline bool fast_parse(std::string_view line, ParsedRow& out) {
    auto take = [&line]() -> std::string_view {
        std::size_t comma = line.find(',');
        if (comma == std::string_view::npos) {
            std::string_view whole = line;
            line = {};
            return whole;
        }
        std::string_view field = line.substr(0, comma);
        line.remove_prefix(comma + 1);
        return field;
    };

    out.type = take();
    if (out.type.empty()) return false;

    std::string_view side = take();
    if (side.empty()) return false;
    out.side = side[0];

    std::string_view price = take();
    if (std::from_chars(price.data(), price.data() + price.size(), out.price).ec != std::errc{})
        return false;

    std::string_view size = take();
    if (std::from_chars(size.data(), size.data() + size.size(), out.size).ec != std::errc{})
        return false;

    out.ts_ns = 0;
    if (!line.empty()) {
        std::string_view ts = take();
        std::from_chars(ts.data(), ts.data() + ts.size(), out.ts_ns);  // optional
    }
    return true;
}

}  // namespace lob
