#include <unitree/robot/go2/video/video_client.hpp>

#include <chrono>
#include <fstream>
#include <iostream>
#include <thread>
#include <vector>
#include <cstdio>

int main(int argc, char** argv)
{
    std::string iface = "eno1";

    if (argc >= 2)
        iface = argv[1];

    std::cout << "Using interface: " << iface << std::endl;

    unitree::robot::ChannelFactory::Instance()->Init(0, iface);

    unitree::robot::go2::VideoClient video_client;
    video_client.SetTimeout(1.0f);
    video_client.Init();

    std::vector<uint8_t> image;

    const std::string tmp_file = "/tmp/go2_camera/latest.tmp.jpg";
    const std::string out_file = "/tmp/go2_camera/latest.jpg";

    system("mkdir -p /tmp/go2_camera");

    while (true)
    {
        image.clear();

        int ret = video_client.GetImageSample(image);

        if (ret == 0 && !image.empty())
        {
            {
                std::ofstream f(tmp_file, std::ios::binary);

                if (f)
                {
                    f.write(
                        reinterpret_cast<const char*>(image.data()),
                        image.size()
                    );
                }
            }

            // 原子替换，避免 ROS2 读到“写了一半”的 JPEG
            std::rename(tmp_file.c_str(), out_file.c_str());

            std::cout
                << "\rJPEG bytes: " << image.size()
                << std::flush;
        }
        else
        {
            std::cerr
                << "\nGetImageSample failed, ret="
                << ret
                << std::endl;
        }

        // 第一版先约 15 Hz，稳定后再提高
        std::this_thread::sleep_for(
            std::chrono::milliseconds(66)
        );
    }

    return 0;
}
