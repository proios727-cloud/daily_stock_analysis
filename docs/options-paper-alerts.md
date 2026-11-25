# 期权权利金纸面告警与过拟合校验（Paper only）

本模块只做纸面跟踪与通知，**不下单、不连接券商**。

## 模块

| 文件 | 职责 |
| --- | --- |
| `src/services/options_validation.py` | Walk-forward + 样本外（OOS）+ 最终留出期校验，输出 `ROBUST` / `FRAGILE` / `OVERFIT` / `INSUFFICIENT_DATA` 判定 |
| `src/services/options_paper_alerts.py` | 纸面持仓状态机：入场、硬止损、移动止损、利润锁定，告警走 `NotificationService.send_with_results(route_type="alert")` |

## 回测器接入契约

`walk_forward(run, grid, start, end, train_days=365, test_days=90, holdout_days=0)` 中的 `run` 为：

```python
run(params: dict, start: date, end: date) -> list[trade]
```

每笔 trade 至少包含 `entry_date`、`exit_date`、`return_pct`（权利金收益率，0.42 表示 +42%），可为 dict 或对象。

判定逻辑（阈值为首版默认值，待真实回测结果校准）：

- 合并 OOS 期望为负，或最终留出期亏损 → `OVERFIT`
- OOS 交易数 < 30、walk-forward 效率（OOS/IS 期望）< 0.5、盈利折数 < 60%、相邻参数盈利占比 < 60% 任一命中 → `FRAGILE`
- 以上均未命中 → `ROBUST`

## 退出规则（按权利金计算）

- 硬止损：`mark <= entry * (1 - stop_loss_pct)`
- 移动止损：浮盈达到 `trail_activation_pct` 后启用，`mark <= peak * (1 - trail_pct)` 退出
- 利润锁定：浮盈达到 `lock_trigger_pct` 后设置不可撤销的地板价 `entry * (1 + lock_floor_pct)`
- 生效止损取三者最高，即移动止损追逐峰值，利润锁定保证最低落袋收益
- 止损判断使用上一根标记前的止损位，避免用同一标记的新高推高止损（无前视）

默认值 `stop_loss_pct=0.35`、`trail_pct=0.25`、`trail_activation_pct=0.20`、`lock_trigger_pct=0.50`、`lock_floor_pct=0.30` 为首版猜测值，应以校验通过的参数替换。

## 使用示例

```python
from src.services.options_paper_alerts import OptionsPaperAlerter, ExitRules

alerter = OptionsPaperAlerter(state_path="data/options_paper_positions.json")
alerter.enter("AAPL 2026-12-19 C 200", "AAPL", 4.10, ExitRules(), note="ma_golden_cross")
alerter.mark({"AAPL 2026-12-19 C 200": 6.40})  # 触发移动止损/利润锁定启用告警
```

通知渠道沿用 `NOTIFICATION_ALERT_CHANNELS` 路由，未新增配置项。单个渠道发送失败不会中断持仓跟踪。
