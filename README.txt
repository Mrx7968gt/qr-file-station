# 1. 解压
mkdir qr-file-station && tar -xzf qr-file-station.tar.gz -C qr-file-station

# 2. 导入镜像
./qr-file-station.sh deploy

# 3. 启动 Web 服务
./qr-file-station.sh start

# 4. 转换文件为二维码
./qr-file-station.sh encode /path/to/files /path/to/output

# 5. 直接在 Linux 终端展示二维码
#    可传原始文件目录，也可传已生成的二维码图片目录
python terminal_qr_browser.py --input-dir /path/to/files
python terminal_qr_browser.py --image-dir /path/to/output
# 如果二维码太宽或太密，调小分块大小
python terminal_qr_browser.py --input-dir /path/to/files --chunk-size 100
# 如果终端窗口很窄，可进一步压缩宽度
python terminal_qr_browser.py --input-dir /path/to/files --chunk-size 100 --module-width 1
# 浏览 PNG 时控制终端显示列宽
python terminal_qr_browser.py --image-dir /path/to/output --image-max-width 80

# 6. 在 ARM 机器上编译 Linux x86_64 可执行文件
chmod +x build_linux_x86_64.sh
./build_linux_x86_64.sh
# 产物:
# dist-linux-x86_64/qr-terminal-browser-linux-x86_64.tar.gz

# 7. 停止服务
./qr-file-station.sh stop