# uv exclude-newer 导致 hermes update 依赖安装失败

首次观察：2026-05-02，hermes update 从 v0.11.x → v0.12.0 (2026.4.30)，11 个新提交。

## 现象

`hermes update` 完成 git pull 后，Python 依赖安装步骤报错退出：

```
× Failed to build `hermes-agent @ file:///Users/…/.hermes/hermes-agent`
├─▶ Failed to resolve requirements from `build-system.requires`
╰─▶ Because there are no versions of setuptools and you require
    setuptools>=61.0, we can conclude that your requirements are
    unsatisfiable.
    hint: `setuptools` was filtered by `exclude-newer` to only include
    packages uploaded before 2026-04-25T03:19:24Z. Consider using
    `exclude-newer-package` to override the cutoff for this package.
```

后续 `hermes --version` 仍可能显示 "Up to date"（因为 git HEAD 已同步），但新功能/依赖不可用。`hermes doctor --fix` 中 croniter 检查也会因 SyntaxError 失败。

## 根因

`~/.hermes/hermes-agent/pyproject.toml` 中：

```toml
[tool.uv]
exclude-newer = "7 days"
```

加上用户配置了 aliyun PyPI 镜像（`~/.config/uv/uv.toml` 中的 `[[index]] url`），该镜像对
setuptools、croniter、python-dateutil、six 等稳定包的上传时间戳陈旧或缺失，导致被过滤。

## 受影响的包（已知）

| 包 | 需求 | 问题 |
|---|---|---|
| setuptools | >=61.0（build-system） | aliyun 镜像时间戳旧 |
| croniter | >=6.0.0,<7（core dep） | aliyun 镜像时间戳旧 |
| python-dateutil | >=2.0（croniter dep） | uv 会错误解析到 1.5（Python 2）|
| six | croniter 间接依赖 | aliyun 镜像时间戳旧 |

## 修复命令

```bash
cd ~/.hermes/hermes-agent

# Step 1：安装受 exclude-newer 过滤的底层包
uv pip install "setuptools>=61.0" "python-dateutil>=2.8.0" "six" \
  --exclude-newer-package "setuptools=false" \
  --exclude-newer-package "python-dateutil=false" \
  --exclude-newer-package "six=false"

# Step 2：安装 croniter（显式豁免其依赖链）
uv pip install "croniter>=6.0.0,<7" \
  --exclude-newer-package "croniter=false" \
  --exclude-newer-package "python-dateutil=false"

# Step 3：重新安装整个项目（豁免已知问题包）
uv pip install -e . \
  --exclude-newer-package "croniter=false" \
  --exclude-newer-package "setuptools=false" \
  --exclude-newer-package "python-dateutil=false" \
  --quiet
```

## 陷阱

### python-dateutil 1.5（Python 2 语法）

uv 在某些解析路径下会安装 `python-dateutil==1.5`，这是 2009 年发布的 Python 2 版本。
安装后 croniter 导入时抛：

```
SyntaxError: invalid syntax
  raise TypeError, "relativedelta only diffs datetime/date"
                 ^
```

**检测**：`uv pip show python-dateutil | grep Version`
**修复**：强制 `>=2.8.0` 并带 `--exclude-newer-package "python-dateutil=false"`

### UV_EXCLUDE_NEWER="" 不可用

环境变量空值触发 parse 报错：

```
error: invalid value '' for '--exclude-newer <EXCLUDE_NEWER>': `` could not be parsed …
```

不要试图用空字符串覆盖，改用 `--exclude-newer-package "pkg=false"` 逐包豁免。

### UV_EXCLUDE_NEWER="2027-01-01" 对 aliyun 镜像无效

即使设为未来日期，aliyun 镜像中部分包元数据缺少 upload_time 字段，仍然无法通过过滤。
必须用 `=false` 格式完全豁免。

### uv --no-extra-index-url 不存在

该 flag 在 uv pip 中不存在，会报 `error: unexpected argument`。

## 验证

```bash
hermes --version           # 应显示最新版本号，不带 "behind"
hermes doctor --fix        # 应看到 ✓ Croniter (cron expressions) (optional)
uv pip show python-dateutil | grep Version  # 应为 2.x
```
