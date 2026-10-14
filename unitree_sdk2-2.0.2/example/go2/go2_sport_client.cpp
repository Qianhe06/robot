#include <cstdint>
#include <iostream>
#include <unistd.h>

#include <unitree/robot/go2/sport/sport_client.hpp>

int main(int argc, char **argv)
{
  if (argc < 2)
  {
    std::cout << "Usage: " << argv[0]
              << " networkInterface" << std::endl;
    return 1;
  }

  unitree::robot::ChannelFactory::Instance()->Init(0, argv[1]);

  unitree::robot::go2::SportClient sport_client;
  sport_client.SetTimeout(2.0f);
  sport_client.Init();

  std::cout << "[1/4] 等待通信稳定..." << std::endl;
  sleep(3);

  // 站立，最多尝试 5 次
  int32_t ret = -1;

  for (int attempt = 1; attempt <= 5; ++attempt)
  {
    std::cout << "[2/4] StandUp，第 "
              << attempt << " 次..." << std::endl;

    ret = sport_client.StandUp();
    std::cout << "返回码: " << ret << std::endl;

    if (ret == 0)
      break;

    sleep(1);
  }

  if (ret != 0)
  {
    std::cerr << "StandUp 失败，停止程序。" << std::endl;
    return 1;
  }

  std::cout << "[3/4] 已站立，等待 4 秒..." << std::endl;
  sleep(4);

  // 趴下，最多尝试 5 次
  ret = -1;

  for (int attempt = 1; attempt <= 5; ++attempt)
  {
    std::cout << "[4/4] StandDown，第 "
              << attempt << " 次..." << std::endl;

    ret = sport_client.StandDown();
    std::cout << "返回码: " << ret << std::endl;

    if (ret == 0)
      break;

    sleep(1);
  }

  if (ret != 0)
  {
    std::cerr << "StandDown 失败，机器人将保持站立。"
              << std::endl;
    return 1;
  }

  sleep(5);
  std::cout << "动作序列完成。" << std::endl;
  return 0;
}
