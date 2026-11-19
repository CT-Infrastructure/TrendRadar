# TrendRadar × Cherry Studio Setup Guide 🍒

**English** | **[中文](README-Cherry-Studio-ZH.md)**

> **Audience:** Users with no programming experience.
> **Client:** Cherry Studio, a free, open-source graphical AI client.

## 1. Download Cherry Studio

Download the Windows or macOS installer from the [official website](https://cherry-ai.com/) or the [latest GitHub release](https://github.com/kangfenmao/cherry-studio/releases/latest). Choose the installer for your operating system and processor.

## 2. Get the project and its news data

The MCP tools need access to the news collected by TrendRadar. The crawler stores that data in the project's `output` directory. Keep a local checkout of this English edition and make sure it can access the data from your deployment before configuring the MCP server.

### Option A: Clone with Git

```bash
git clone https://github.com/CT-Infrastructure/TrendRadar.git
cd TrendRadar
```

If your own fork contains the data you need, clone that fork instead. Run `git pull` later to update tracked files. If your deployment stores data remotely, use TrendRadar's storage synchronization features; cloning the source code alone does not download remote storage.

### Option B: Download a ZIP

1. Open [CT-Infrastructure/TrendRadar](https://github.com/CT-Infrastructure/TrendRadar), or your own fork.
2. Select **Code → Download ZIP**.
3. Extract the archive to a permanent folder.

You can also download the [master branch ZIP](https://github.com/CT-Infrastructure/TrendRadar/archive/refs/heads/master.zip) directly. To update a ZIP installation, download a new copy and preserve your configuration and `output` data.

## 3. Install the MCP server

### Windows

1. Double-click `setup-windows.bat` in the project folder. `setup-windows-en.bat` remains available as an English compatibility entry point.
2. Wait for installation to finish.
3. Record the command path and arguments shown by the installer.

### macOS

1. Open **Terminal**.
2. Drag `setup-mac.sh` from the project folder into the terminal window.
3. Press **Return**.
4. Record the command path and arguments shown by the installer.

## 4. Configure Cherry Studio

1. Open Cherry Studio's **Settings**.
2. Find **MCP**, then select **Add**.
3. Enter the command and arguments printed by the setup script. Use the actual paths from your installation.
4. Save the configuration and turn on the server's switch in the MCP server list.

## 5. Verify the connection

Ask Cherry Studio:

```text
Fetch the latest news with TrendRadar.
```

Other examples:

```text
Search the last 3 days of news for "artificial intelligence".
Find Tesla reports from January 2025.
Analyze the popularity trend for "iPhone".
```

The available results depend on the data collected by your deployment. When you give a relative date such as "the last 3 days", the date-resolution tool calculates the date range.

A working connection should let the AI call TrendRadar tools and return collected news with source platforms, headlines, and rankings. See the [MCP tool guide](README-MCP-FAQ.md) for more examples.

## Optional: HTTP mode

HTTP mode can serve clients separately from the STDIO launcher. Start the server before connecting a client.

On Windows, double-click `start-http.bat`. On macOS or Linux, run:

```bash
./start-http.sh
```

Then configure Cherry Studio with:

```text
Type: streamableHttp
URL: http://localhost:3333/mcp
```

This URL addresses a server on the same computer. Remote access requires a separately configured host and network connection; the default local endpoint is not a public service.
