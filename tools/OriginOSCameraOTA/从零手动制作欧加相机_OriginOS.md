# 从零手动制作欧加相机 OriginOS 模块

更新：2026-09-26。本文记录本轮一加13 / OriginOS DSU 的真实做法，并区分可复用的思路与必须重新适配的硬件、ABI。APK 自身的反编译、修改、签名过程按用户要求不展开，使用配套成品应用素材。

## 0. 范围与完成标准

“从零”指没有安装旧模块也能从目标系统原包、本机欧加原厂 ROM、随工具提供的兼容素材生成新模块，不是让用户先刷旧模块再提取。

本轮 stage9 用户已反馈新版 OK。已修项目包括普通拍摄、变焦、美颜、尺寸、滤镜、慢动作、独立杜比、字体与 vivo 缩略图；这不等于所有模式所有场景都逐项实验过。ProXDR 完整 ColorOS 私有 EDR 仍未确认。AI 配套组件此前漏包，本次正在补齐，联网 AI 不能只凭 APK 已安装宣布完成。

不可把一加13的 vendor/odm、镜头名、tag 编号、二进制偏移直接套在其他机型上。跨型号适配必须重做对应步骤。当前高通规则也不意味着联发科已支持。

## 1. 准备三类材料

### A：目标 OriginOS 原包

保留分区目录结构，优先读取：

- `system/bin/cameraserver`
- `system/lib64/libcamera_client.so`
- `system/lib64/libcameraservice.so`、`libvivocameraservice.so`（用于确认实际实现与调用关系，不默认替换）
- `system/lib64/libandroid.so`、`libnativewindow.so`
- `system/lib64/libstagefright_bufferqueue_helper.so`
- `system/lib64/libstagefright.so`
- 原生 codec XML、linker 配置、SELinux CIL、各分区属性。

这些核心文件必须来自目标系统版本。历史补丁保存的是修改逻辑，不能把上一版系统库当目标原件。OTA 后重新提取并定位。

### B：本机欧加原厂 ROM

保留完整 vendor/odm，以及 system、system_ext、product 和 my_* 中相机有关配置、库、字体。重点读取：

- HAL：`vendor/lib64/hw/camera.qcom.so`、实际存在的 CHI override 及其依赖。
- `odm/etc/camera/` 中传感器、相机配置和算法相关文件。
- `odm/etc/camera/config/oplus_camera_config`。
- 与该机型配套的美颜 JNI、APS 算法、Dolby 配置。

离线无法确定的 tag 可选用原厂系统的 HAL 导出或 dumpsys 补充；不要求构建时必须连手机。缺镜头资料时报告具体缺项，不把 dodge 清单当所有欧加手机的镜头表。

### C：工具随附的兼容素材

成品相机/相册/AppPlatform、三个独立 JAR、CRD 私有相机服务配套库、已说明来源的兼容库、权限 XML、字体，以及 AI 完整应用组。它是可分发素材，不是要求用户自备“完整基础模块”。闭源文件的可分发性需由发布者另行确认。

## 2. 建立模块目录与取材清单

模块根至少含 `module.prop`、`customize.sh`、`service.sh`、`system.prop`，以及需要时的 `sepolicy.rule`。文件映射：

| 系统目标 | ZIP 内位置 |
|---|---|
| `/system/bin/cameraserver` | `system/bin/cameraserver` |
| `/system/lib64/...` | `system/lib64/...` |
| `/system_ext/...` | `system/system_ext/...` |
| `/product/...` | `system/product/...` |
| `/vendor/...` | `system/vendor/...` |
| `/odm/...` | `system/odm/...` |

记录每一个输出文件的原件来源、修改脚本、目的和验证状态。不要把开发电脑的绝对路径写进手机运行脚本。探针、Frida、测试 jar、/data/local/tmp 文件不属于运行依赖，不打进正式模块。

现成逐文件材料表在历史工作目录 `stage1_sources.json`、`stage4_sources.json`、`stage5_changes.json`，后续阶段是在其基础上覆盖，不能只看 stage1 判断最终来源。

## 3. 先让应用和独立 JAR 正常加载

1. 放入成品相机、相册与 AppPlatform；保留各应用自身 `lib/arm64` 私有依赖。
2. 在 `system/system_ext/framework/` 放 `oplus-fwk.jar`、相机 unit sdk、adaptor 三组实际素材对应 JAR。
3. 在同分区 `etc/permissions` 保留它们的 shared-library XML，XML 的库名、文件路径、依赖关系应与应用声明匹配。
4. 不把这三个 JAR 随手合并进目标系统厂商 JAR，不默认覆盖 bootclasspath.pb。声明存在不等于进程已加载，须查 PackageManager shared libraries、应用 uses-libraries 和类加载异常。
5. 本轮相册与 vivo 的 Provider authority 有冲突，使用已经修好共存的相册成品。不能重新换回未经共存处理的旧 APK。

本轮依据：`build_stage1.py`、`prepare_gallery.py`、`patch_gallery_authority.py`、`build_stage3.py`。

## 4. 恢复相机真实包名到 HAL 的传递

只有大师/XPAN正常不代表 APS 原生链已经接通。先查实际执行的 CameraDeviceClient 和 Camera3Device 实现，不只凭同名符号所在文件猜。

本轮在 `CameraDeviceClient::endConfigureLocked` 已做权限及会话参数检查之后、调用设备 configureStreams 之前，针对真实客户端 `com.oplus.camera` 写入 `com.oplus.packageName` 元数据。不要全局伪装所有相机包名，也不要用跨线程全局变量保存上一个客户端身份。

步骤：

1. 获取本机 HAL 导出的 tag 编号与数据类型；本轮是 byte 类型、`0x8117002e`，这只是本轮实例。
2. 反汇编目标函数，追 this、sessionParams、operatingMode 的实际寄存器/栈槽；对象字段也要通过构造/getter 证明，不能沿用旧偏移。
3. 在正确 metadata 对象上更新包名，维持原始调用、返回值与被保存寄存器。
4. 如果后续过滤函数会删除该 tag，再做单项保留；不把所有未支持 vendor tag 全放行。
5. 严格区分 helper 的代码与数据岛：LDR literal 读的应是真正的 tag 数值；MOVZ/MOVK 必须是执行路径。不能把机器码当 tag 传给 HAL。

本轮手工实现：`package_inject.S`、`build_package_inject.py`。其常量地址仅用于那份原件，自动工具仍需动态重写这一部分。

## 5. 私有 YUV 与后处理（绿片重点）

本轮目标系统有两份相关 lockPlanes 实现：`libandroid.so` 和 `libnativewindow.so`。只修一份可能出现某个进程好、另一个进程仍绿。

处理原则：沿用目标系统自身 ABI，只给欧加私有像素格式补正确的平面分类/访问分支，保留原生处理。不整体替换 ColorOS 的公共 libui/libgui，不用后期滤镜掩盖错读 YUV，也不靠跳过全部后处理避免崩溃。

手工步骤：

1. 从实际错误栈确定 lockPlanes 走哪份库，检查私有格式的分支。
2. 比对 y/cb/cr 地址、行步长、色度步长的构造和使用关系。
3. 以 `private_yuv.S` 的已分析逻辑为参考，在新库中重新定位入口和恢复分支。
4. 两份提供者都生成目标版本补丁；保持原库依赖，不改其他像素格式路径。
5. 普通拍照前后摄、后处理前后、图库最终图都检查；缩略图正常不能代表最终 JPEG 正常。

本轮脚本 `build_private_yuv.py`；算法依赖由 `prepare_aps_test.py` / `aps_test` 材料与 `build_stage2.py` 接入。算法/标定必须按本机原厂取材。

## 6. SAT 变焦采用私有桥，不替换全部系统相机服务

目标系统标准 Binder 路径保持原生；欧加私有 SAT 请求转交已配套的 CRD 扩展链。这样减少对系统相机和其他应用的影响，但不意味着无需 OTA 适配。

本轮输出：

- 当前系统 `libcamera_client.so` 复制成 `libcamera_ori.so`，同步改 SONAME。
- 构建 `libsatbridge.so`，依赖该原生私有副本；cameraserver 对 client 的依赖改为桥接库。
- CRD 配套私有服务库在 `system/lib64`，`libcsextimpl.so` 在 `system/system_ext/lib64`，具体列表由 `stage4_sources.json` 记录。

不要把桥内部用于私有命令的 client 与处理目标系统标准事务的 client 混为一谈。复制库名、SONAME、DT_NEEDED 都要成套处理。必须验证一加相机物理镜头切换，以及其他相机对焦/变焦没有被串改。

实现依据：`build_sat_bridge.py`、`sat_bridge.c`、`prepare_sat_probe.py`、`build_stage4.py`。本机 dodgemain 等字符串不能泛化为别的机型相机 ID。

## 7. 拍摄尺寸、RAW、XPAN、时间戳、统计值

这些不是 UI 或缩略图尺寸调整，修改的是服务/HAL 协商路径。

- `roundBufferDimensionNearest`：识别真实实现和 CFI/BTI 跳板；按 RAW 与 XPAN 实际输出分支补正确尺寸，不全局返回固定分辨率。
- Vivo 流统计某些路径用 int32 范围检查拒绝欧加 streamUseCase，但内存字段和 Parcel 仍为64位。本轮只去掉四个错误范围中止分支，保留完整值与其他检查。
- returnBuffer：保留 HAL 自己上报的错误，只跳过框架仅因重复/回退时间戳制造的错误；不伪造所有时间戳或吞掉所有 HAL 错误。

本轮都在 `package_inject.S` / `build_package_inject.py` 的后续版中。每个功能要查新 ABI；旧脚本中的固定地址不是通用规则。

## 8. 视频、60fps、慢动作与独立杜比

### 视频缓冲

分析目标 `libstagefright_bufferqueue_helper.so` 的 GraphicBufferSource 构造，定位真实 consumer 和 allowUnlimitedSlots 调用。依据 `build_gbs_unlimited.py` 保留新版本对象布局，不能套旧寄存器。

### 编解码注册与配置

v0.1.1 取材修正：检查本机 /odm/etc/media_codecs_dolby_vision.xml、/vendor/etc/media_codecs_dolby_vision.xml 和各 codec 根文件的 Include 链。media_codecs_dolby_vision_playback.xml 是我们曾生成的兼容文件，不是原厂必备文件；纯 ADB 模式不能因为它缺失就中止。

解析 XML 的有效 Decoders/MediaCodec、Encoders/MediaCodec，取 c2.qti.dv.decoder、可选 secure decoder、c2.qti.dv.encoder；保留每项 Limit/Feature。不启用注释里的条目，不把其他机型的 8K、帧率、码率能力写进来。按本机各根 XML 的递归 Include 结果，只为尚未注册的条目生成补充文件，再在原根关闭节点前增加 Include。若原文件已注册 decoder 而没有 encoder，只补 encoder。实现位于 dolby_config.py，每个条目的原始路径写入构建报告。

本机没有任何有效 Dolby 编码/解码声明时，报告具体缺项，要求补原厂文件；不能仅靠随便造一个空 XML 消除错误。只有真正缺少的可选路径可以跳过，权限拒绝和 ADB 断开不是“文件不存在”。

从目标系统已有 codec XML 开始增量修改，保留原定义与 Include 链；只有缺失时才添加与本机 SoC 匹配的 Dolby 编解码器配置。不要把 `media_codecs_sun*.xml` 当所有机型必备名字。

本轮独立配置来自之前验证可用的同机型材料，分别补 `/system/etc`、`/vendor/etc`、`/vendor/persist/display` 中对应 Dolby 配置；它们打在相机模块里，不再要求用户启用自动亮度模块。跨机型优先从用户原厂包取。

合并辅助摄像头包名列表，保留原值再添加 com.oplus.camera。FMQ、HDR/Dolby属性仅按已证实的用途使用；不能因某属性“看起来相关”就覆盖整机显示策略。

### 修录制 RPU 的长度前缀

`libstagefright.so` 必须用目标版本。定位 `MPEG4Writer::addSample_l` 的 raw sample 写入：实测普通 NAL 已为长度前缀，但末尾 RPU 仍是 Annex-B，导致欧加播放器/FFmpeg报 Invalid NAL unit size。

helper 验证 NAL 边界、VCL、末尾 RPU 类型和长度，仅将该四字节起始码替换为实际剩余长度。不重编码、不删除 RPU，不变动索引与样本大小。

### 修 vivo 绿色缩略图（必须保留）

不改 vivo 相册 APK，在 `MediaCodec::configure` 请求入口限定：

- Vivo 缩略图标记 `vivo.vdec.thumbnail.mode.value=1`；
- 解码 flags=0、没有 crypto/descrambler；
- DV profile=256（profile8）、基础层 compatibility=4；
- decoder=c2.qti.dv.decoder，MIME=video/dolby-vision。

匹配后仅把此次配置改为 MIME video/hevc、profile2（Main10）、color-transfer-request3（SDR抽帧）。正常播放和不匹配请求不变，不 reset codec、不清回调。仅调整 transfer 而保留 DV MIME 的测试仍绿，不能删掉 MIME/profile 处理。

已经纳入工具 `media_patch.py`：依据函数/调用参数动态定位，按当前 ELF 计算 helper VA 与文件偏移，二者不假定相等。运行输出库与 `media_patch_report.json`。本机离线构建成功并通过6项定位测试；动态工具生成的新布局仍需与手工 stage9 区分验证。

## 9. 美颜 JNI 必须匹配 ODM 实现

本轮旧通用预览 JNI 直接访问不匹配的 Slender 参数 ABI，换用匹配 ODM `libFaceBeautyJni` 的 Product JNI 后恢复。

原件 `libApsFaceBeautyPreviewProductJni.so`，本轮以 `libApsFaceBeautyPreviewJni.so` 名称放三处，避免命名空间加载到旧副本：

- `system/lib64/`
- `system/system_ext/lib64/`
- `system/system_ext/priv-app/OplusCamera/lib/arm64/`

依据 `solidify_beauty.py`。其他机型先验证导出 JNI 契约、DT_NEEDED 与 ODM 调用签名，不因名字相同就替换。

## 10. 配置、超级文本、字体与滤镜

- `oplus_camera_config`：保留原机全部配置项，只增加经确认缺失的 export super text 开关。电脑 AES 解密正确不代表应用能读回；本轮 native UpdateHelper 实测把重封装内容当文本，最终使用其支持的明文 JSON 容器。依据 `fix_stage6_config.py`，不可重跑旧 stage5 后把明文修复覆盖丢。
- 滤镜：使用已说明来源与兼容修改的配套 libmsnativefilter，不盲换有验证/契约差异的官方版本；库要按实际命名空间放置。
- 字体：把 OPSans-En-Regular.ttf、SysSans-En-Regular.ttf、SysSans-Hans-Regular.ttf、SysSans-Hant-Regular.ttf 放 `system/fonts`。本轮使用既有验证素材。不覆盖 fonts.xml 或系统默认字体；机型名是另一回事。

## 11. AI 组件必须成组补齐

除了相机、相册、OplusAppPlatform，还要带：

| 组件 | 模块路径 | 注意 |
|---|---|---|
| AIUnit | `system/system_ext/priv-app/AIUnit/` | APK 与整个 lib/arm64 一起搬 |
| StdID | `system/system_ext/priv-app/StdID/` | 身份服务；跨系统是否成功须验证 |
| VideoGallery | `system/system_ext/priv-app/VideoGallery/` | 包括视频处理所需私有库 |
| GalleryPermissions | `system/system_ext/app/MioOplusGalleryPermissions/` | 配套权限声明辅助 APK，不等于系统授权全部成功 |

对应同分区 default-permissions、privapp-permissions、hiddenapi XML 一并保留。工具 `ai_components.py` 已提取四组共34个文件（包括三份XML），记录每个来源；现有 OriginOS 专用 XML 不被盲覆盖，需确认声明实际覆盖。

本轮 stage9 此前遗漏这四组，不能以旧 XML 中存在包名说 APK 已补。组装完成后检查包安装、服务绑定、权限定义、native dlopen、模型下载、网络服务响应。AI 消除/补光/最佳表情应逐项验证，任何一项成功不代表全部成功。

## 12. 权限、标签、安装脚本

1. 用独特 default-permissions 文件名授予已声明的运行时权限；privapp XML 放在特权应用所在分区。
2. Android 原生 default-permissions XML 不等于任意 AppOps 设置。需要的 AppOps 由安装/开机一次性脚本设置，仅限定模块应用，不能写成永久轮询。
3. 安装时和启动时设置 root:root、目录0755、文件0644、system_file；cameraserver是0:2000、0755、cameraserver_exec。vendor/odm库使用目标policy中相应类型，本轮为same_process_hal_file；配置使用vendor_configs_file。
4. 创建相机专属数据目录并设合理属主/权限、restorecon；不要把通用 /data 全部放宽。
5. SELinux 基于目标真实类型和调用链合并，保留原有已验证规则。不要声明不存在的type，不给 untrusted_app 全局权限，不把permissive测试成功等同于enforcing通过。
6. 交给正常元模块挂载；这次不重走失败的自挂载/叠 overlayfs 方案。安装成功之后仍要检查进程实际映射和类加载，不能只看模块目录有文件。

## 13. 打包与回归

清点APK/JAR/SO/配置/字体/脚本，输出来源和修改报告。打包时不要夹入旧版本目录、电脑路径、探针、临时日志或原图。构建端可以检查patch语义，但不在手机启动脚本放无用的文件哈希锁或机型序列号锁。

测试建议顺序：启动及授权→普通拍照/前后摄→后处理成片→变焦物理镜头→美颜→RAW/XPAN/其他尺寸→60fps/慢动作→杜比录制全段解码→两种相册播放/缩略图→水印字体→AI→清数据后授权→SELinux严格模式（只有用户允许且具备恢复措施时）。

DSU重启前确认 `gsi_tool enable` 与 status 成功，避免跑回主系统。不要清日志掩盖错误，不删除用户样片。读取图片最终文件验证，不能只看取景器或文件大小。

## 14. 本轮证据入口

工作目录：`analysis/originos17_oplus_camera_20260926/`。

- `手动适配记录.md`、`18点后运行验证.md`
- `尺寸水印慢动作独立杜比文本_第五阶段.md`
- `权限与相册HDR_第六七阶段.md`
- `杜比封装与缩略图_第八阶段.md`
- `build_stage1.py` 至 `build_stage9.py` 及其调用的 .py/.S/.c
- `font_after_boot.jpg`、`dolby_native_fixed.mp4`、`vivo_native_format_only.png`

本教程覆盖当前已知完整链条，仍需随着自动工具的逐项落地补充动态定位规则与各机型验证记录；它不是对任意ROM可不经适配运行的保证。
