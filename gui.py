"""B站评论区智能分析器 - GUI界面"""

import sys
import os
import json
import threading
import queue

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog

import config
from crawler import crawl_comments, save_comments
from analyzer import analyze_comments, save_analysis


class PrintRedirector:
    """将print输出重定向到消息队列，供GUI显示"""

    def __init__(self, q: queue.Queue):
        self.queue = q
        self._stdout = sys.stdout

    def write(self, text):
        self._stdout.write(text)
        if text.strip():
            self.queue.put(("log", text))

    def flush(self):
        self._stdout.flush()


class BilibiliAnalyzerGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("B站视频评论区智能分析器")
        self.root.geometry("1050x800")
        self.root.minsize(900, 650)

        self.msg_queue = queue.Queue()
        self.analysis_result = None
        self.comments_data = None
        self.is_running = False

        self._setup_ui()
        sys.stdout = PrintRedirector(self.msg_queue)
        self._poll_queue()

    def __del__(self):
        sys.stdout = sys.__stdout__

    # ---- UI 构建 ----

    def _setup_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        main = ttk.Frame(self.root, padding="10")
        main.pack(fill=tk.BOTH, expand=True)

        # 标题
        ttk.Label(main, text="B站视频评论区智能分析器",
                  font=("Microsoft YaHei", 16, "bold")).pack(pady=(0, 10))

        # 配置区
        cfg = ttk.LabelFrame(main, text="配置", padding="10")
        cfg.pack(fill=tk.X, pady=(0, 10))

        # Cookie
        ttk.Label(cfg, text="B站 Cookie:").grid(row=0, column=0, sticky=tk.NW, pady=2)
        cookie_frame = ttk.Frame(cfg)
        cookie_frame.grid(row=0, column=1, sticky=tk.EW, pady=2, padx=(5, 0))
        self.cookie_text = tk.Text(cookie_frame, height=2, wrap=tk.WORD)
        self.cookie_text.pack(fill=tk.BOTH, expand=True)
        if config.BILIBILI_COOKIE:
            self.cookie_text.insert("1.0", config.BILIBILI_COOKIE)
        ttk.Label(cfg, text="已预填默认Cookie，可修改或清空").grid(
            row=0, column=2, sticky=tk.N, padx=(5, 0))

        # API Key
        ttk.Label(cfg, text="DeepSeek API Key:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.apikey_var = tk.StringVar(value=config.DEEPSEEK_API_KEY or "")
        self.apikey_entry = ttk.Entry(cfg, textvariable=self.apikey_var, width=60)
        self.apikey_entry.grid(row=1, column=1, sticky=tk.EW, pady=2, padx=(5, 0))
        ttk.Label(cfg, text="已预填默认Key，可修改").grid(
            row=1, column=2, sticky=tk.W, padx=(5, 0))

        # BV号 + 按钮
        bv_frame = ttk.Frame(cfg)
        bv_frame.grid(row=2, column=0, columnspan=3, sticky=tk.EW, pady=(8, 0))

        ttk.Label(bv_frame, text="BV号:").pack(side=tk.LEFT)
        self.bv_var = tk.StringVar()
        self.bv_entry = ttk.Entry(bv_frame, textvariable=self.bv_var, width=18,
                                  font=("Consolas", 11))
        self.bv_entry.pack(side=tk.LEFT, padx=(5, 10))
        self.bv_entry.bind("<Return>", lambda e: self.start_analysis())

        self.analyze_btn = ttk.Button(
            bv_frame, text="开始分析", command=self.start_analysis, width=12)
        self.analyze_btn.pack(side=tk.LEFT)

        self.progress_var = tk.StringVar(value="就绪")
        ttk.Label(bv_frame, textvariable=self.progress_var,
                  foreground="#2563eb").pack(side=tk.LEFT, padx=(10, 0))

        cfg.columnconfigure(1, weight=1)

        # 选项卡
        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # 日志
        log_frame = ttk.Frame(self.notebook, padding="5")
        self.notebook.add(log_frame, text="运行日志")
        self.log_text = scrolledtext.ScrolledText(
            log_frame, wrap=tk.WORD, font=("Consolas", 9), state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 结果
        result_frame = ttk.Frame(self.notebook, padding="5")
        self.notebook.add(result_frame, text="分析结果")
        self.result_text = scrolledtext.ScrolledText(
            result_frame, wrap=tk.WORD, font=("Microsoft YaHei", 10),
            state=tk.DISABLED)
        self.result_text.pack(fill=tk.BOTH, expand=True)

        # 结果文本标签
        for name, font, color in [
            ("h1", ("Microsoft YaHei", 15, "bold"), "#1a1a2e"),
            ("h2", ("Microsoft YaHei", 12, "bold"), "#2c3e50"),
            ("h3", ("Microsoft YaHei", 10, "bold"), "#333"),
            ("body", ("Microsoft YaHei", 10), "#333"),
            ("bar", ("Consolas", 9), "#555"),
        ]:
            self.result_text.tag_configure(name, font=font, foreground=color)
            self.result_text.tag_configure(
                name, lmargin1=10, lmargin2=10, spacing1=2, spacing3=2)

        # 底部按钮
        bottom = ttk.Frame(main)
        bottom.pack(fill=tk.X)

        self.save_analysis_btn = ttk.Button(
            bottom, text="保存分析结果 (JSON)", command=self._save_analysis,
            state=tk.DISABLED)
        self.save_analysis_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.save_comments_btn = ttk.Button(
            bottom, text="保存评论数据 (JSON)", command=self._save_comments,
            state=tk.DISABLED)
        self.save_comments_btn.pack(side=tk.LEFT)

        ttk.Button(bottom, text="退出", command=self.root.destroy).pack(side=tk.RIGHT)

        # 状态栏
        self.status_var = tk.StringVar(value="就绪 — 输入BV号后点击「开始分析」")
        ttk.Label(main, textvariable=self.status_var, relief=tk.SUNKEN,
                  anchor=tk.W).pack(fill=tk.X, pady=(5, 0))

    # ---- 消息轮询 ----

    def _poll_queue(self):
        try:
            while True:
                msg_type, msg = self.msg_queue.get_nowait()
                if msg_type == "log":
                    self._append_log(msg)
                elif msg_type == "result":
                    self.analysis_result = msg
                    self._display_result(msg)
                    self.save_analysis_btn.config(state=tk.NORMAL)
                    self.save_comments_btn.config(state=tk.NORMAL)
                    self.analyze_btn.config(state=tk.NORMAL)
                    self.progress_var.set("分析完成")
                    self.status_var.set("分析完成 — 可保存结果")
                    self.is_running = False
                elif msg_type == "comments":
                    self.comments_data = msg
                elif msg_type == "error":
                    self._append_log(f"\n[错误] {msg}\n")
                    self.analyze_btn.config(state=tk.NORMAL)
                    self.progress_var.set("出错")
                    self.status_var.set(f"错误: {msg}")
                    self.is_running = False
                    self.notebook.select(0)
                elif msg_type == "progress":
                    self.progress_var.set(msg)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _append_log(self, text: str):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    # ---- 启动分析 ----

    def start_analysis(self):
        if self.is_running:
            return

        bv = self.bv_var.get().strip()
        if not bv:
            messagebox.showwarning("输入错误", "请输入BV号")
            return

        cookie = self.cookie_text.get("1.0", tk.END).strip()
        apikey = self.apikey_var.get().strip()

        # 清空之前的结果
        for w in (self.result_text, self.log_text):
            w.config(state=tk.NORMAL)
            w.delete("1.0", tk.END)
            w.config(state=tk.DISABLED)

        self.save_analysis_btn.config(state=tk.DISABLED)
        self.save_comments_btn.config(state=tk.DISABLED)
        self.analysis_result = None
        self.comments_data = None
        self.is_running = True
        self.analyze_btn.config(state=tk.DISABLED)
        self.progress_var.set("正在分析...")
        self.status_var.set("正在爬取评论...")
        self.notebook.select(0)

        threading.Thread(target=self._run_analysis,
                         args=(bv, cookie, apikey), daemon=True).start()

    def _run_analysis(self, bv: str, cookie: str, apikey: str):
        try:
            # 更新运行时配置（crawler模块内也有独立引用，需同步更新）
            import crawler
            config.BILIBILI_COOKIE = cookie
            crawler.BILIBILI_COOKIE = cookie
            crawler._session = None
            crawler._wbi_key = None

            # Step 1: 爬取
            print(f"开始爬取视频 {bv} 的评论...\n")
            comments_data = crawl_comments(bv)
            self.msg_queue.put(("comments", comments_data))

            if not comments_data.get("comments"):
                print("\n[提示] 该视频暂无评论或评论获取失败。")
                self.msg_queue.put(("error", "该视频暂无评论"))
                return

            save_comments(comments_data)

            # Step 2: 分析
            if not apikey:
                print("\n[警告] 未配置DeepSeek API Key，跳过分析。")
                self.msg_queue.put(("error", "未配置DeepSeek API Key"))
                return

            print("\n开始分析评论...\n")
            analysis = analyze_comments(comments_data, apikey)
            save_analysis(analysis, bv)
            self.msg_queue.put(("result", analysis))

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.msg_queue.put(("error", str(e)))

    # ---- 结果展示 ----

    def _display_result(self, analysis: dict):
        w = self.result_text
        w.config(state=tk.NORMAL)
        w.delete("1.0", tk.END)

        if "error" in analysis:
            w.insert(tk.END, f"分析出错: {analysis['error']}\n", "body")
            w.config(state=tk.DISABLED)
            return

        vi = analysis.get("video_info", {})
        title = vi.get("title", "未知")
        analyzed = analysis.get("analyzed_comment_count", 0)
        total = analysis.get("total_comment_count", 0)
        batches = analysis.get("batch_count", 1)
        failed = analysis.get("failed_batch_count", 0)

        w.insert(tk.END, "📊 分析报告\n", "h1")
        w.insert(tk.END, f"视频: {title}\n", "body")
        w.insert(tk.END, f"分析评论数: {analyzed}/{total}  |  批次: {batches}",
                 "body")
        if failed:
            w.insert(tk.END, f"  |  失败批次: {failed}", "body")
        w.insert(tk.END, "\n\n")

        # 整体氛围
        atm = analysis.get("overall_atmosphere", "未知")
        atm_desc = analysis.get("atmosphere_description", "")
        w.insert(tk.END, "🎭 整体氛围\n", "h2")
        w.insert(tk.END, f"  {atm}\n", "body")
        if atm_desc:
            w.insert(tk.END, f"  {atm_desc}\n", "body")
        w.insert(tk.END, "\n")

        # 观点分布
        viewpoints = analysis.get("viewpoints", [])
        if viewpoints:
            w.insert(tk.END, "📋 观点分布\n", "h2")
            for vp in viewpoints:
                name = vp.get("viewpoint", "未知")
                pct = vp.get("percentage", 0)
                desc = vp.get("description", "")
                bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                w.insert(tk.END, f"  {bar} {pct}%  {name}\n", "bar")
                if desc:
                    w.insert(tk.END, f"         {desc}\n", "body")
            w.insert(tk.END, "\n")

        # 舆论导向
        direction = analysis.get("public_opinion_direction", "")
        if direction:
            w.insert(tk.END, "🧭 舆论导向\n", "h2")
            if isinstance(direction, dict):
                for k, v in direction.items():
                    w.insert(tk.END, f"  • {k}: {v}\n", "body")
            else:
                w.insert(tk.END, f"  {direction}\n", "body")
            w.insert(tk.END, "\n")

        # 总结
        summary = analysis.get("summary", "")
        if summary:
            w.insert(tk.END, "📝 总结\n", "h2")
            w.insert(tk.END, f"  {summary}\n", "body")

        # 原始分析（解析失败时）
        raw = analysis.get("raw_analysis", "")
        if raw:
            w.insert(tk.END, "\n[原始分析]\n", "h3")
            w.insert(tk.END, f"  {raw[:2000]}\n", "body")

        w.config(state=tk.DISABLED)
        self.notebook.select(1)

    # ---- 保存 ----

    def _save_analysis(self):
        if not self.analysis_result:
            return
        bv = self.bv_var.get().strip()
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("全部", "*.*")],
            initialfile=f"{bv}_analysis.json" if bv else "analysis.json",
            title="保存分析结果")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.analysis_result, f, ensure_ascii=False, indent=2)
            self.status_var.set(f"分析结果已保存至: {path}")
            messagebox.showinfo("保存成功", f"分析结果已保存至:\n{path}")

    def _save_comments(self):
        if not self.comments_data:
            return
        bv = self.bv_var.get().strip()
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("全部", "*.*")],
            initialfile=f"{bv}_comments.json" if bv else "comments.json",
            title="保存评论数据")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.comments_data, f, ensure_ascii=False, indent=2)
            self.status_var.set(f"评论数据已保存至: {path}")
            messagebox.showinfo("保存成功", f"评论数据已保存至:\n{path}")

    # ---- 启动 ----

    def run(self):
        self.root.mainloop()


def main():
    app = BilibiliAnalyzerGUI()
    app.run()


if __name__ == "__main__":
    main()
