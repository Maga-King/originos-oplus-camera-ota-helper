# 欧加相机 OriginOS OTA 助手

[![构建完整 Windows 工具](https://github.com/Maga-King/originos-oplus-camera-ota-helper/actions/workflows/build.yml/badge.svg)](https://github.com/Maga-King/originos-oplus-camera-ota-helper/actions/workflows/build.yml)

面向欧加硬件移植 OriginOS 的相机兼容工具。基于用户提供的**目标 OriginOS 原包**和**本机欧加原厂 ROM**，读取镜头、vendor tag、配置与二进制，生成独立相机模块。

**Windows 原生，不依赖 WSL；不会自动刷入、重启或上传手机信息。** 当前发布版为 **v0.1.2 开发预览版**，不是所有机型已验证的通用成品。

## 下载与使用

优先到 [完整云端成品](https://github.com/Maga-King/originos-oplus-camera-ota-helper/releases/tag/ci-4) 下载 `OriginOSCameraOTA-windows-complete.zip`，需要源码时再取同一 Release 的源码 ZIP。完整解压后运行 `OriginOSCameraOTA/OriginOSCameraOTA.exe`，不要只复制 EXE。

`v0.1.2-preview` Release 的 `OriginOSCameraOTA-v0.1.2-preview-windows-full.zip` 同时保留为完整初始便携包及后续 CI 的固定素材来源。

1. 选择目标 OriginOS 解包目录、本机 ColorOS/OxygenOS 解包目录。
2. 原件不足时可主动开启 ADB 备用取材；有多台设备时明确选定设备。只构建离线模块无需连接手机。
3. 选择构建缓存目录，填写可选作者名；留空默认为“科比”。
4. 构建结束弹出 Windows“另存为”窗口。取消另存不会丢失缓存中的成品，可再次保存。
5. 自行检查报告、备份原模块并刷入测试。本工具不自动安装模块。

纯 ADB 模式不能凭空取得未运行的 ColorOS 公共分区；使用原厂 vendor/odm，不把当前 OriginOS 的 system/product 冒充原厂文件。ADB 读取的已挂载系统库可能是旧补丁件，OTA 重适配优先使用新系统原件。

## 已包含的工作

- cameraserver 包名注入、SAT 桥、尺寸/XPAN/RAW/时间戳适配；区分 AArch64 literal 数据岛与指令，避免把 MOV 机器码当 vendor tag。
- camera client 私有副本、YUV/视频缓冲兼容；按函数及指令语义定位，不以旧整文件替换冒充动态修补。
- 基于目标 libstagefright 的 Dolby RPU 封装及 vivo 杜比缩略图基础层请求修补，不修改 vivo 相册 APK。
- 按本机 vendor/odm 的 codec 声明和 Include 链构建独立 Dolby 配置，不依赖自动亮度模块，不写死旧版 playback XML 必须存在。
- 独立 OPlus JAR、相机/相册/AI 应用组、字体、XML 权限、SELinux 增量与安装/开机标签处理。
- 中文 GUI、共享核心的 JSON CLI、来源清单与构建报告；成品保存位置和 module.prop 作者可自选。

没有强制要求用户手动提供 dumpsys。真正必须的是可靠的 vendor tag 编号/类型与镜头映射；只有离线索引、不能确认运行时编号时，需要 dumpsys 或其他可靠 HAL 导出，不能猜测硬编码。

## 验证边界

v0.1.1 曾完成一加 13 OriginOS 只读 ADB 完整构建验证。v0.1.2 新增作者与另存窗口，仅完成编译发布，按使用者要求交由其测试。静态构建成功不等于所有模式已实机通过。

跨机型美颜 ABI、严格 SELinux 回归、AI 联网服务、完整 ColorOS 私有 EDR/ProXDR 仍需验证；不保证所有 OTA、联发科平台或不同摄像头硬件即刷即用。保留无法可靠定位时的错误报告，不冒充适配完成。

## 源码与开发

仓库保存源码、补丁汇编、兼容素材目录清单、许可证和中文教程。大型 APK/SO、基线及 Windows 工具链放在完整 Release，不占用 Git 历史。

```text
tools/OriginOSCameraOTA/   GUI、CLI、OriginOS 规则、教程
tools/OplusCameraOTA/     复用的 ELF、ROM、配置及策略读取模块
```

使用 Python 3.12（带 Tk），先安装 `requirements.txt`，再从 Release 恢复构建素材：

```powershell
python -m pip install -r requirements.txt
python restore_release_materials.py --portable "D:\解压目录\OriginOSCameraOTA"
cd tools/OriginOSCameraOTA
python entrypoint.py
# 重新构建 Windows 便携版：
python -m PyInstaller --noconfirm OriginOSCameraOTA.spec
```

CLI 可执行 `python entrypoint.py --project 项目.json --result 结果.json`，JSON 字段以 `sources.py` 的 `Inputs` 和 `entrypoint.py` 为准。Release 已包含运行时，不要求普通用户安装 Python、NDK 或 WSL。

详细链路见 [从零手动制作](tools/OriginOSCameraOTA/从零手动制作欧加相机_OriginOS.md)、[开发记录与限制](tools/OriginOSCameraOTA/README.md)。用户设备缓存、原始抓取日志、照片、凭据和签名私钥不在发布包中。

## GitHub Actions 完整构建

在 Actions 中运行“构建完整 Windows 工具”。工作流从固定 Release 恢复全部闭源组件、兼容基线、Windows LLVM 和 ADB，然后安装 Python 依赖、执行不依赖真机的测试、**重新编译本仓库的 EXE**，最后打包全部运行材料。输出 `OriginOSCameraOTA-windows-complete.zip`，不是只有几 MB 的空壳程序。

默认同时把本轮完整成品和当前源码发布为独立 `ci-运行序号` Release，避免构建附件过期后找不到成品；不需要此行为可在手动运行时关闭“同时发布”。

已实际通过 [Actions 36462659280](https://github.com/Maga-King/originos-oplus-camera-ota-helper/actions/runs/36462659280)：22 项测试通过，6 项缺少开发设备原始媒体库而明确跳过；Windows EXE 重新编译、冻结程序启动、完整打包与 Release 上传成功。没有使用 WSL，也没有连接手机。

完整发布包同时作为可下载工具和 CI 材料包，正常构建不依赖作者电脑路径、旧模块文件夹或 WSL。新用户只需另外提供其目标系统和原厂 ROM；用户专属完整 ROM 不在仓库分发。CI 构建完成不代表对新机型进行了拍摄测试。

## 致谢与权利说明

感谢 **kde_yyds** 对 crDroid / AlphaDroid 欧加相机移植的贡献，感谢 **ColorOS陈稀**。

- [dodge-camera-port/patches-crdroid](https://github.com/dodge-camera-port/patches-crdroid)
- [dodge-camera-port/vendor_oplus_camera](https://github.com/dodge-camera-port/vendor_oplus_camera)
- [AlphaDroid 一加 13 设备树](https://github.com/AlphaDroid-devices/android_device_oneplus_dodge)
- [此前的 HyperOS OTA 助手](https://github.com/Maga-King/oplus-camera-ota-helper)

第三方 APK/JAR/SO、算法及工具链仍属于各自权利人，源码公开不改变其许可或授予新的转授权。详见 [第三方说明](tools/OriginOSCameraOTA/THIRD_PARTY.md) 及随附 licenses。
