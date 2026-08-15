# 转换引擎：ppt-master

本管线把每一页渲染成 SVG 后，由 **[ppt-master](https://github.com/hugohe3/ppt-master)** 的
`svg_to_pptx` 转成 **PowerPoint 原生可编辑 .pptx**（真实形状 / 表格 / 图表 / 阴影 / 误差棒）。

## 为什么需要

- **SVG 生成**（`pptagent.svggen`）与**预览渲染**（`render_preview.py`）**不依赖**引擎，
  纯 Python + cairosvg 即可。
- **PPTX 转换**（`pptagent.run` 的 Stage 4）与**质量门**（Stage 3 的
  `svg_quality_checker.py`）需要引擎的 scripts。

## 安装

```bash
# 在 pptagent 仓库的同级目录克隆（默认引擎路径是 ../ppt-master）
git clone https://github.com/hugohe3/ppt-master.git ../ppt-master

# 引擎自身依赖
pip install python-pptx lxml
```

## 使用

```bash
# 显式指定引擎路径
python3 -m pptagent.run examples/demo_deck_plan.json \
    --out exports/demo --engine ../ppt-master
```

不传 `--engine` 时按 `../ppt-master` 查找；找不到会给出引导。

## 换引擎

`ppt-master` 的 scripts 位于 `<仓库>/skills/ppt-master/scripts`，核心两个脚本：

- `svg_quality_checker.py` —— 质量门（本管线 Stage 3）
- `svg_to_pptx.py` —— SVG → PPTX 转换（本管线 Stage 4）

只要你的引擎提供同样的 scripts 接口，即可替换 `--engine` 指向它。
