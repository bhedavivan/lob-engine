cmake_minimum_required(VERSION 3.15)
project(lob_engine CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(orderbook STATIC src/order_book.cpp)
target_include_directories(orderbook PUBLIC include)

add_executable(lob_engine src/main.cpp)
target_link_libraries(lob_engine PRIVATE orderbook)

enable_testing()
add_executable(test_order_book tests/test_order_book.cpp)
target_link_libraries(test_order_book PRIVATE orderbook)
add_test(NAME order_book_tests COMMAND test_order_book)
