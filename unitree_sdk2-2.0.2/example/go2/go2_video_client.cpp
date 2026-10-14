#include <unitree/robot/go2/video/video_client.hpp>
#include <unitree/robot/channel/channel_factory.hpp>

#include <opencv2/opencv.hpp>

#include <cstdint>
#include <iostream>
#include <vector>

int main(int argc, char **argv)
{
    if (argc < 2)
    {
        std::cerr << "Usage: " << argv[0]
                  << " <network_interface>" << std::endl;
        return 1;
    }

    // 只初始化通信并读取摄像头，不发送运动命令
    unitree::robot::ChannelFactory::Instance()->Init(0, argv[1]);

    unitree::robot::go2::VideoClient video_client;
    video_client.SetTimeout(1.0f);
    video_client.Init();

    std::vector<uint8_t> image_data;

    cv::namedWindow("Go2 Camera", cv::WINDOW_NORMAL);
    cv::resizeWindow("Go2 Camera", 960, 540);

    std::cout << "Camera preview started." << std::endl;
    std::cout << "Press Q or Esc in the video window to quit." << std::endl;

    while (true)
    {
        image_data.clear();

        const int32_t ret = video_client.GetImageSample(image_data);
        if (ret != 0)
        {
            std::cerr << "GetImageSample failed, ret = "
                      << ret << std::endl;

            // 即使暂时取图失败，也允许按键退出
            const int key = cv::waitKey(10);
            if (key == 'q' || key == 'Q' || key == 27)
                break;

            continue;
        }

        if (image_data.empty())
        {
            std::cerr << "Received an empty image." << std::endl;
            continue;
        }

        cv::Mat frame = cv::imdecode(image_data, cv::IMREAD_COLOR);
        if (frame.empty())
        {
            std::cerr << "JPEG decoding failed." << std::endl;
            continue;
        }

        cv::imshow("Go2 Camera", frame);

        const int key = cv::waitKey(30);
        if (key == 'q' || key == 'Q' || key == 27)
            break;

        // 点击窗口关闭按钮时退出
    }

    cv::destroyAllWindows();
    std::cout << "Camera preview stopped safely." << std::endl;
    return 0;
}
