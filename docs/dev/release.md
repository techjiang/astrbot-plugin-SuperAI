# 发布流程

## 版本号

- 版本号单一来源：[`superai/version.py`](../../superai/version.py) 的 `__version__`；
- 展示用版本同时写在 [`metadata.yaml`](../../metadata.yaml) 的 `version` 字段；
- 仓库健康检查会校验两者一致；
- 遵循[语义化版本](https://semver.org/lang/zh-CN/)：`vMAJOR.MINOR.PATCH`。

## 分支模型

| 分支 | 用途 |
| --- | --- |
| `main` | 发布基线，永远是可用状态 |
| `feat/*` / `fix/*` / `docs/*` / `chore/*` | 短期分支，通过 PR 合入 `main` |

自 v0.2.3 起，`main` 即发布基线；版本发布直接推 `main` 并打 tag。

## 发布检查清单

1. **功能与质量**

   ```bash
   ruff check .
   ruff format --check .
   python -m pytest tests
   ASTRBOT_REF=/tmp/astrbot-ref python scripts/e2e_smoke.py
   ```

   全绿，且 CI（lint + 单测 + 真实框架联调）通过。

2. **版本与元数据**
   - [ ] `superai/version.py` 与 `metadata.yaml` 的版本号一致且已递增；
   - [ ] `metadata.yaml` 的 `desc` / `short_desc` 是否仍需更新。

3. **文档**
   - [ ] `CHANGELOG.md` 增加新版本段落，按 `修复 / 增强 / 变更 / 安全` 分类；
   - [ ] 新增或变更的配置项已写入 `_conf_schema.json` 的 `hint`
         与 [配置手册](../configuration.md)；
   - [ ] 新增或变更的指令已写入 [指令手册](../commands.md)；
   - [ ] README 的「当前版本」链接与能力表格同步。

4. **打 Release**
   - [ ] 在 CNB 平台创建 Release，tag 指向 `main` 的发布提交；
   - [ ] Release 说明直接引用 `CHANGELOG.md` 对应段落；
   - [ ] 若有破坏性变更，在 Release 说明里单独用「⚠️ 升级注意」列出。

## CHANGELOG 写法

```markdown
## vX.Y.Z

一句话说明这一版的主线（修了什么类型的问题 / 加了什么能力）。

### 修复

- **现象**。原因（要写到能让人看懂为什么原来会坏）。现在怎么改的。

### 增强

- 新增能力与配置项。

### 变更（Breaking）

- 行为/配置变更，以及用户需要做什么。
```

经验：**「静默失效」类问题一定要写清「为什么没有报错」**，
否则后人会以为只是普通的逻辑 bug，改回去又坏一遍。

## 发布说明归档

历史发布说明放在 [`docs/releases/`](../releases/README.md)，
每个版本一个文件，便于从 Issue / 群里直接给人链接。

## 相关文档

- [开发与测试](./development.md)
- [贡献指南](../../CONTRIBUTING.md)

---

**最后核对**：`v0.2.4`（逐条对照 `superai/version.py` 与测试断言，无凭空描述）
