from glob import glob
from setuptools import find_packages, setup

package_name = "go2_scissors_search"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        (
            "share/ament_index/resource_index/packages",
            ["resource/" + package_name],
        ),
        (
            "share/" + package_name,
            ["package.xml"],
        ),
        (
            "share/" + package_name + "/launch",
            glob("launch/*.launch.py"),
        ),
        (
            "share/" + package_name + "/config",
            glob("config/*.yaml"),
        ),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="user",
    maintainer_email="user@localhost",
    description="Go2 autonomous scissors search",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "time_sync_node = "
            "go2_scissors_search.time_sync_node:main",
            "odom_tf_node = "
            "go2_scissors_search.odom_tf_node:main",
            "motion_adapter_node = "
            "go2_scissors_search.motion_adapter_node:main",
            "mission_manager_node = "
            "go2_scissors_search.mission_manager_node:main",
        ],
    },
)
