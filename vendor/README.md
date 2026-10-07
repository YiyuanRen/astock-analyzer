# vendor/

本目录存放以源码形式内置(vendored)的第三方依赖。

## chan.py

- 来源: [Vespa314/chan.py](https://github.com/Vespa314/chan.py)
- 内置 commit: `429d6ed3043e27c93a003ba2b10e70a05575e1f5`
- 内置方式: `git clone --depth 1` 后移除其 `.git`, 源码直接提交进本仓库
- 用途: A股缠论多级别计算(笔/线段/中枢/买卖点/MACD背驰), 原生支持 `lv_list` 多级别区间套
- 导入方式: 运行时将 `vendor/chan.py` 加入 `sys.path`, 再 `from Chan import CChan`
- 依赖: 仅核心计算用到 `numpy` / `pandas`; 其 `Plot/` 画图模块需要 `matplotlib`(本项目不使用)
- 为何 vendoring 而非 pip: chan.py 未发布到 PyPI, 且需固定版本保证缠论计算结果可复现

### 更新方式

如需升级, 重新 clone 对应 commit 覆盖本目录, 更新上面的 commit 记录, 并重跑 `scripts/smoke_chan.py` 验证。
