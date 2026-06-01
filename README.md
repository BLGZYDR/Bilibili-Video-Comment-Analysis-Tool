# B站评论区智能分析器  
## 项目简介  
本项目是一个功能完整的 **B站（Bilibili）视频评论区分析工具**，集评论爬取、AI 智能分析与可视化展示于一体。它能够自动抓取指定 B站视频的全部评论（包括楼中楼子回复），并通过调用 **DeepSeek API** 进行深度语义分析，生成结构化的舆情分析报告，包含整体氛围评估、观点分布统计、舆论导向分析等。  

## 项目架构  
```  
B站视频评论区智能分析器/  
├── gui.py             # GUI 图形界面 — Tkinter 桌面应用  
├── crawler.py         # 爬虫模块 — B站评论 WBI 签名 + 游标分页抓取  
├── analyzer.py        # 分析模块 — DeepSeek API 批次分析 + 合成  
├── config.py          # 全局配置 — API Key、Cookie、爬取参数  
├── requirements.txt   # 依赖管理  
├── .env               # 环境变量配置（非公开）  
└── output/            # 输出目录 — JSON 格式存储评论与分析结果  
    ├── {BV号}_comments.json   # 原始评论数据  
    └── {BV号}_analysis.json   # AI 分析报告  
```
## 使用说明  
执行gui.py，在弹出界面里填写deepseekAPI，模型，B站cookie之后点击执行即可


