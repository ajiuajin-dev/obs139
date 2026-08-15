"use strict";

const { Plugin, MarkdownView, Notice, PluginSettingTab, Setting } = require("obsidian");
const { spawn } = require("child_process");
const http = require("http");

const DEFAULT_SETTINGS = {
	// 139 上传工具的 Python 脚本路径（默认为本机固定路径，可在设置里改）
	scriptPath: "D:/obs139/139uplink.py",
	// 本地图片代理脚本（自动启动，供直链渲染）
	serverScript: "D:/obs139/139img.py",
	// 代理监听端口
	port: 8740,
	// Python 可执行文件（Windows 上一般就是 python）
	pythonBin: "python",
};

function isServerUp(port) {
	return new Promise((resolve) => {
		const req = http.get({ host: "127.0.0.1", port, path: "/health", timeout: 800 }, (res) => {
			res.resume();
			resolve(true);
		});
		req.on("error", () => resolve(false));
		req.on("timeout", () => { req.destroy(); resolve(false); });
	});
}

module.exports = class Obs139Uploader extends Plugin {
	async onload() {
		await this.loadSettings();
		this.ensureServer();

		this.addCommand({
			id: "upload-image-to-139",
			name: "上传图片到 139 并插入链接",
			callback: () => this.pickImageAndUpload(),
		});

		this.addRibbonIcon("image-plus", "上传图片到 139", () => this.pickImageAndUpload());

		this.addSettingTab(new Obs139SettingTab(this.app, this));
	}

	/** 确保本地图片代理 139img.py 在运行（没起就自动拉起，detached 独立进程） */
	ensureServer() {
		isServerUp(this.settings.port).then((up) => {
			if (up) return;
			try {
				const child = spawn(this.settings.pythonBin, [this.settings.serverScript], {
					detached: true,
					windowsHide: true,
					stdio: "ignore",
				});
				child.unref();
				new Notice("🖼️ 已启动 139 本地图片代理");
			} catch (e) {
				console.error("[obs139] 启动代理失败", e);
			}
		});
	}

	async loadSettings() {
		this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
	}

	async saveSettings() {
		await this.saveData(this.settings);
	}

	/** 弹系统文件选择框，选图后上传 */
	pickImageAndUpload() {
		const input = document.createElement("input");
		input.type = "file";
		input.accept = "image/*";
		input.addEventListener("change", () => {
			const file = input.files && input.files[0];
			if (!file) return;
			const filePath = this.getFilePath(file);
			if (!filePath) {
				new Notice("无法获取文件路径（新版 Electron 不暴露 File.path）", 5000);
				return;
			}
			this.upload(filePath);
		});
		input.click();
	}

	/** 取系统文件的绝对路径：旧 Electron 用 file.path，新版用 webUtils.getPathForFile */
	getFilePath(file) {
		if (file && file.path) return file.path;
		try {
			const { webUtils } = window.require("electron");
			const p = webUtils.getPathForFile(file);
			if (p) return p;
		} catch (e) { /* 继续尝试下一方式 */ }
		try {
			const { webUtils } = require("electron");
			const p = webUtils.getPathForFile(file);
			if (p) return p;
		} catch (e) { /* 见下 */ }
		return null;
	}

	/** 调 Python 工具上传，实时显示进度，成功后把链接插入当前光标处 */
	upload(imagePath) {
		const statusItem = this.addStatusBarItem();
		statusItem.setText("📤 139 上传 0%");
		const args = [this.settings.scriptPath, imagePath, "--link-only"];
		const proc = spawn(this.settings.pythonBin, args, { windowsHide: true });

		let stdout = "";
		let stderr = "";
		const updateProgress = () => {
			// Python 端进度形如 "\r  上传中 123/456 KB (27%)"，取最后一个百分比
			const matches = [...stderr.matchAll(/(\d+)%/g)];
			if (matches.length > 0) {
				statusItem.setText(`📤 139 上传 ${matches[matches.length - 1][1]}%`);
			}
		};

		proc.stdout.on("data", (d) => {
			stdout += d.toString("utf-8");
		});
		proc.stderr.on("data", (d) => {
			stderr += d.toString("utf-8");
			updateProgress();
		});
		proc.on("error", (err) => {
			statusItem.remove();
			new Notice("139 上传失败：" + err.message, 8000);
		});
		proc.on("close", (code) => {
			statusItem.remove();
			const lines = (stdout || "").trim().split("\n").filter((l) => l.length > 0);
			const link = lines[lines.length - 1] || "";
			if (code !== 0 || !/^!?\[/.test(link)) {
				const msg = (stderr || "").trim().split("\n").filter(Boolean).pop() || "未知错误";
				new Notice("139 上传失败：" + msg, 8000);
				return;
			}
			this.insertAtCursor(link);
			new Notice("✅ 已插入 139 图片链接", 4000);
		});
	}

	insertAtCursor(text) {
		const view = this.app.workspace.getActiveViewOfType(MarkdownView);
		if (!view) {
			new Notice("请先打开一篇笔记再上传", 4000);
			return;
		}
		const editor = view.editor;
		editor.replaceSelection(text + "\n");
	}
};

class Obs139SettingTab extends PluginSettingTab {
	constructor(app, plugin) {
		super(app, plugin);
		this.plugin = plugin;
	}

	display() {
		const { containerEl } = this;
		containerEl.empty();

		containerEl.createEl("h2", { text: "139 Uploader 设置" });

		new Setting(containerEl)
			.setName("Python 脚本路径")
			.setDesc("139uplink.py 的绝对路径")
			.addText((text) =>
				text
					.setPlaceholder("D:/obs139/139uplink.py")
					.setValue(this.plugin.settings.scriptPath)
					.onChange(async (value) => {
						this.plugin.settings.scriptPath = value.trim() || DEFAULT_SETTINGS.scriptPath;
						await this.plugin.saveSettings();
					})
			);

		new Setting(containerEl)
			.setName("Python 可执行文件")
			.setDesc("Windows 一般为 python")
			.addText((text) =>
				text
					.setPlaceholder("python")
					.setValue(this.plugin.settings.pythonBin)
					.onChange(async (value) => {
						this.plugin.settings.pythonBin = value.trim() || DEFAULT_SETTINGS.pythonBin;
						await this.plugin.saveSettings();
					})
			);

		new Setting(containerEl)
			.setName("本地图片代理端口")
			.setDesc("139img.py 监听端口，改完需重启插件")
			.addText((text) =>
				text
					.setPlaceholder("8740")
					.setValue(String(this.plugin.settings.port))
					.onChange(async (value) => {
						const p = parseInt(value.trim(), 10);
						this.plugin.settings.port = Number.isFinite(p) && p > 0 ? p : DEFAULT_SETTINGS.port;
						await this.plugin.saveSettings();
					})
			);

		containerEl.createEl("p", {
			text: "提示：token / 默认上传文件夹 在 D:/obs139/config.json 里配置。",
		});
	}
}
