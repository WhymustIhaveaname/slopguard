# slopguard

一个 Claude Code 插件: Claude 每次回复完, 自动匹配 AI 特有词, 发现就当场打回, 塞一句提示词让它用人话重说。

## 原理

挂在 Claude Code 的 `Stop` hook 上(一轮回答完毕、即将把控制权交还那一刻):

1. 取出最后一条消息;
2. 用词库(正则)逐条扫描;
3. 没命中 --> 放行;
4. 命中 --> 从 `default-templates.txt` 中随机抽一句话让它用人话重说.

## 安装

### 从 GitHub 安装

在任意 Claude Code 会话里:

```
/plugin marketplace add WhymustIhaveaname/slopguard
/plugin install slopguard@slopguard
```

### 从本地目录安装

把本地仓库目录注册成 marketplace,再安装:

```
/plugin marketplace add /路径/到/slopguard
/plugin install slopguard@slopguard
```

指向的是本地目录,改完代码 `/plugin marketplace update slopguard` 即生效,但别移动该目录。

### 临时挂载

```
claude --plugin-dir /路径/到/slopguard
```

## 自定义

插件自带的默认词库 / 模板在 `data/` 下(随插件分发,只读)。你自己的词和模板写在:

- `~/.claude/slopguard/patterns.txt` —— 你的 AI 腔正则,一行一条
- `~/.claude/slopguard/templates.txt` —— 你的回注模板,一行一条

这两个文件首次运行时自动创建, 运行时合并使用。
