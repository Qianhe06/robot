/**
 * @file subscribe_pointcloud.cpp
 * @brief Subscribe the pointcloud published from DDS topic
 * @date 2023-11-23
 */

#include <unitree/robot/channel/channel_subscriber.hpp>
#include <unitree/common/time/time_tool.hpp>
#include <unitree/idl/ros2/PointCloud2_.hpp>

#define TOPIC_CLOUD "rt/utlidar/cloud"

using namespace unitree::robot;
using namespace unitree::common;

#include <iostream>
#include <cstring>
#include <algorithm>

void Handler(const void *message)
{
    const auto *cloud_msg =
        static_cast<const sensor_msgs::msg::dds_::PointCloud2_ *>(message);

    const auto &fields = cloud_msg->fields();
    const auto &data = cloud_msg->data();

    int x_offset = -1;
    int y_offset = -1;
    int z_offset = -1;

    // 找到 x y z 在每个点中的字节偏移
    for (const auto &field : fields)
    {
        std::cout << "field: " << field.name()
                  << ", offset: " << field.offset()
                  << ", datatype: " << static_cast<int>(field.datatype())
                  << ", count: " << field.count()
                  << std::endl;

        if (field.name() == "x")
            x_offset = field.offset();
        else if (field.name() == "y")
            y_offset = field.offset();
        else if (field.name() == "z")
            z_offset = field.offset();
    }

    if (x_offset < 0 || y_offset < 0 || z_offset < 0)
    {
        std::cout << "Cannot find x/y/z fields!" << std::endl;
        return;
    }

    uint32_t point_step = cloud_msg->point_step();
    uint32_t point_count = cloud_msg->width() * cloud_msg->height();

    std::cout << "point count = " << point_count << std::endl;
    std::cout << "point step = " << point_step << std::endl;

    // 先只打印前 20 个点
    uint32_t print_count = std::min(point_count, 20u);

    for (uint32_t i = 0; i < print_count; ++i)
    {
        size_t base = static_cast<size_t>(i) * point_step;

        float x, y, z;

        std::memcpy(&x, &data[base + x_offset], sizeof(float));
        std::memcpy(&y, &data[base + y_offset], sizeof(float));
        std::memcpy(&z, &data[base + z_offset], sizeof(float));

        std::cout << "Point[" << i << "] "
                  << "x = " << x
                  << ", y = " << y
                  << ", z = " << z
                  << std::endl;
    }
}



int main(int argc, const char **argv)
{
  if (argc < 2)
  {
    std::cout << "Usage: " << argv[0] << " networkInterface" << std::endl;
    exit(-1);
  }

  ChannelFactory::Instance()->Init(0, argv[1]);

  ChannelSubscriber<sensor_msgs::msg::dds_::PointCloud2_> subscriber(TOPIC_CLOUD);
  subscriber.InitChannel(Handler);

  while (true)
  {
    sleep(10);
  }

  return 0;
}
