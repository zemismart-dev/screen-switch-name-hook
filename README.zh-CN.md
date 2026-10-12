# Screen Switch Name Hook 客户安装说明

版本：2.3.0

[Zemismart 官网](https://www.zemismart.com/) · [English](README.md) · [更新记录](CHANGELOG.md) · [支持型号 JSON](SUPPORTED_MODELS.json)

## 关于 Zemismart

Zemismart 提供智能开关、窗帘及卷帘电机、照明、传感器和网关等智能家居产品。访问 [Zemismart 官网](https://www.zemismart.com/)了解产品。

[![在 Home Assistant 中打开 HACS 仓库](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=zemismart-dev&repository=screen-switch-name-hook&category=integration)

## 用途

本集成把 Home Assistant 中每一路开关实体的“名称”同步到 Zemismart 屏显开关的实体屏幕，可由 Zigbee（ZHA）和 Matter 设备共用：

- Matter 继续负责开关控制，屏幕名称通过设备的 uHome 局域网协议写入。
- Zigbee 继续由 ZHA 控制，屏幕名称通过设备 endpoint 1 的 Tuya MCU `0xEF00` 名称 DP 写入。

Matter 客户不需要填写设备 IP。集成会根据 Home Assistant Matter 设备的 operational network ID，通过 `_matter._tcp.local` 精确发现当前 IPv4 地址，并在写入前校验设备 MAC、SPID 和有效 endpoint 集合。Zigbee 设备不使用 IP，但必须已经通过 ZHA 接入并具有正确的 Tuya 名称 DP 映射。

## 支持的 Matter 型号

所有已验证型号的 Matter VID 均为 `5020 / 0x139C`。

| 路数 | Matter PID | uHome SPID | 有效 endpoint |
| --- | --- | --- | --- |
| 1 路 | `43832 / 0xAB38` | `4929 / 0x1341` | `1` |
| 2 路 | `43833 / 0xAB39` | `4930 / 0x1342` | `1, 2` |
| 3 路 | `43840 / 0xAB40` | `4927 / 0x133F` | `1, 2, 3` |
| 4 路 | `43831 / 0xAB37` | `4928 / 0x1340` | `1, 2, 3, 4` |

请勿只按路数猜测 PID；4 路 PID 为 `0xAB37`。SPID 不是 Matter PID。

## Zigbee（ZHA）支持范围

Zigbee 与 Matter 使用同一个集成入口和同一个 `zha_namehook.sync_entity_name` 操作，但底层协议不同。ZHA 默认名称 DP 为：

| 通道 | 默认名称 DP |
| --- | --- |
| 1 | `105` |
| 2 | `106` |
| 3 | `107` |
| 4 | `108` |

只有抓包、设备文档或现有 quirk 已确认采用这些 DP 的 Tuya/Zemismart 屏显开关才能直接使用。其他 Zigbee 型号必须在 `configuration.yaml` 中填写实体通道和名称 DP，不能只根据“几路开关”盲写：

```yaml
zha_namehook:
  entity_channels:
    switch.screen_switch_1: 1
    switch.screen_switch_2: 2
  entity_name_dps:
    switch.screen_switch_1: 106
    switch.screen_switch_2: 107
```

修改 YAML 后重启 Home Assistant。界面中已经添加的集成无需删除。不同型号的 DP 可能不同，上面的 `106/107` 仅演示如何覆盖，不代表通用值。

## 安装

### HACS 安装（推荐）

1. 确认 Home Assistant 已安装并配置 HACS。
2. 点击上方“在 Home Assistant 中打开 HACS 仓库”按钮。
3. 如果没有自动打开，在 HACS 中添加自定义仓库：
   `https://github.com/zemismart-dev/screen-switch-name-hook`，类别选择“集成”。
4. 在 HACS 中下载 `Screen Switch Name Hook`。
5. 重启 Home Assistant Core。
6. 打开“设置 → 设备与服务 → 添加集成”。
7. 搜索 `Screen Switch Name Hook` 或“屏显开关名称同步”，点击提交。
8. 确认目标开关已经通过 Matter 集成或 ZHA 正常配对，并能分别控制每一路。

### 手动安装

1. 从 GitHub Releases 下载发布 ZIP，或下载仓库源码。
2. 把 `custom_components/zha_namehook` 整个目录复制到 Home Assistant 的 `/config/custom_components/`。
3. 重启 Home Assistant Core。
4. 打开“设置 → 设备与服务 → 添加集成”，搜索并添加 `Screen Switch Name Hook`。

老版本使用 `configuration.yaml` 的客户仍可保留 `zha_namehook:`；新客户不需要修改 YAML。

## 修改屏幕名称

推荐方法：打开“设置 → 设备与服务 → 实体”，进入某一路开关实体，修改该实体的“名称”并保存。Matter 会优先按 entity unique ID 取得 endpoint；ZHA 会使用 YAML 映射，未配置时才按常见实体 ID 后缀推断通道。

请注意：只修改 `entity_id`（例如把 `switch.smart_switch_switch_1` 改成另一个实体 ID）不等于修改实体“名称”，不会触发屏显同步。

也可以在“开发者工具 → 操作”中调用：

```yaml
action: zha_namehook.sync_entity_name
data:
  entity_id: switch.smart_switch_switch_1
  name: 客厅灯
```

省略 `name` 时会使用实体当前名称。`channel` 通常不需要填写，仅用于特殊情况下手动覆盖 endpoint。

ZHA 还可以直接调用专用操作：

```yaml
action: zha_namehook.set_display_name
data:
  ieee: "00:11:22:33:44:55:66:77"
  channel: 1
  name: 客厅灯
```

这里的 IEEE 必须替换成客户自己的 ZHA 设备地址；不要把真实 IEEE 发布到公开日志或工单。

## 名称规则

- 自动去除首尾空格。
- Matter uHome 名称最多 12 个 Unicode code point；超过部分会截断，不会从中文 UTF-8 字节中间截断。
- 不允许空名称。
- 不做 Unicode normalization。
- Emoji 和少数字符能否正确显示取决于设备字体。
- Matter 每次只写一个 endpoint，并在写入后读取设备状态确认完全相等。
- ZHA 名称以 UTF-8 原始字节写入 Tuya DP；实际长度和字体限制取决于该 Zigbee 固件。

## 网络要求

- 下列网络要求仅适用于 Matter uHome 路由；Zigbee 路由只要求设备正常连接到同一套 ZHA。
- Home Assistant 主机与 Matter 开关必须处于可互通的可信局域网。
- 网络必须允许 Matter mDNS `_matter._tcp.local`。
- 网络必须允许 Home Assistant 到设备的 UDP `3678`。
- 不支持访客 Wi-Fi/AP 隔离，跨 VLAN 时需正确转发 mDNS 和 UDP 3678。
- uHome UDP 3678 是无加密、无鉴权的厂家私有协议，不应暴露到互联网。

## Matter 可靠性与安全保护

- 不保存固定 IP；设备 DHCP 地址变化后会按精确 Matter mDNS 身份重新绑定。
- 缓存 IP 超时时先在同一 IP 快速复查，再刷新 mDNS。
- 写入 ACK 丢失时先读取屏幕名称；只有读回一致才判定成功。
- 同一路连续改名使用 latest-wins，排队中的旧名称不会覆盖新名称。
- 写入前后严格校验回复来源 IP/端口、消息类型、请求 ID、success、MAC、SPID 和有效 endpoint 集合。
- 名称协议不会下发 Matter OnOff；名称测试后仍应按现场安全要求确认开关状态。

## 故障排查

1. 在“设置 → 系统 → 日志”搜索 `zha_namehook`。
2. 在集成页面下载诊断；诊断不包含设备 IP、MAC、Matter operational ID 或名称。
3. Matter：确认开关控制正常，并确认 mDNS 与 UDP 3678 未被防火墙拦截。
4. Zigbee：确认使用 ZHA、设备存在输入 cluster `0xEF00`，并核对该型号真实名称 DP。
5. Matter 不要只看 HTTP 成功；以日志中的 `uHome screen name verified` 和设备回读为准。
6. ZHA 日志 `Tuya ZHA display name sent` 表示帧已交给 Zigbee 栈，不等同于设备屏幕回读；交付时需要观察实体屏幕确认。

## 已验证范围

- Home Assistant Core：`2026.8.0`
- Home Assistant OS：`17.3`
- 实机：2 路 Matter PID `0xAB39`、uHome SPID `0x1342`、固件观察值 `3.7`
- 1/3/4 路参数来自同一已验证开发交付矩阵，但本次 Home Assistant 客户版发布前未在这台公司 HA 上逐台回归。
- ZHA 路由沿用原有 Tuya `0xEF00` 名称 DP 实现；具体 Zigbee 型号是否支持必须以该型号抓包/DP 表和实体屏幕验证为准。

正式批量交付前，建议每个 PID 至少在目标网络与目标 HA 版本上完成一次实体改名、屏幕观察、状态读回和 DHCP 地址变化测试。
