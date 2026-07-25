from setuptools import setup, find_packages

setup(
    name="bambu-mqtt-generator",
    version="1.0.0",
    description="Generate MQTT JSON payloads for Bambu Lab printer AMS/external spool management",
    author="",
    packages=find_packages(),
    include_package_data=True,
    package_data={
        "bambu_mqtt_generator": ["config/**/*.json"],
    },
    python_requires=">=3.8",
    install_requires=[
        "cryptography>=3.4",
    ],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
)