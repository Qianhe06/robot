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

void Handler(const void *message)
{
  const sensor_msgs::msg::dds_::PointCloud2_ *cloud_msg = (const sensor_msgs::msg::dds_::PointCloud2_ *)message;
  std::cout << "Received a raw cloud here!"
            << "\n\tstamp = " << cloud_msg->header().stamp().sec() << "." << cloud_msg->header().stamp().nanosec()
            << "\n\tframe = " << cloud_msg->header().frame_id()
            << "\n\tpoints number = " << cloud_msg->width()
            << std::endl
            << std::endl;
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
