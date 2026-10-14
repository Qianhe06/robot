project(test)
cmake_minimum_required(VERSION 3.5)

SET(CMAKE_CXX_STANDARD 17)

include_directories(/usr/local/include/ddscxx /usr/local/include/iceoryx/v2.0.2)
link_libraries(unitree_sdk2 ddsc ddscxx rt pthread)

add_executable(test_point test_point.cpp)
add_executable(subscribe_pointcloud subscribe_pointcloud.cpp)
add_executable(get_photo get_photo.cpp)
