// Platform detection
class PlatformDetector {
    static detect() {
        const userAgent = navigator.userAgent;
        const platform = navigator.platform;

        // Detect OS
        let os = 'unknown';
        let arch = 'unknown';

        // OS Detection
        if (platform.includes('Win') || userAgent.includes('Windows')) {
            os = 'windows';
        } else if (platform.includes('Mac') || userAgent.includes('Mac')) {
            os = 'darwin';
        } else if (platform.includes('Linux') || userAgent.includes('Linux')) {
            os = 'linux';
        }

        // Architecture Detection
        if (platform.includes('arm64') || platform.includes('aarch64')) {
            arch = 'arm64';
        } else if (platform.includes('x86_64') || platform.includes('x64') || platform.includes('Intel')) {
            arch = 'x86_64';
        } else if (platform.includes('i386') || platform.includes('i686')) {
            arch = 'x86_64'; // Fallback for 32-bit
        }

        // Additional checks for ARM on Windows
        if (os === 'windows' && userAgent.includes('ARM')) {
            arch = 'arm64';
        }

        // Apple Silicon detection
        if (os === 'darwin' && (platform.includes('arm') || userAgent.includes('arm'))) {
            arch = 'arm64';
        }

        // Fallback for MacIntel (Intel Mac)
        if (os === 'darwin' && platform.includes('Intel')) {
            arch = 'x86_64';
        }

        return { os, arch };
    }

    static getPackageName(os, arch) {
        const packageMap = {
            'darwin-x86_64': 'mnemo-darwin-x86_64',
            'darwin-arm64': 'mnemo-darwin-arm64',
            'linux-x86_64': 'mnemo-linux-x86_64',
            'linux-arm64': 'mnemo-linux-arm64',
            'windows-x86_64': 'mnemo-windows-x86_64.exe',
            'windows-arm64': 'mnemo-windows-arm64.exe'
        };

        const key = `${os}-${arch}`;
        return packageMap[key] || null;
    }

    static getInstallCommand(os) {
        if (os === 'windows') {
            return 'irm https://github.com/zhuqingyv/mnemo/releases/latest/download/install.ps1 | iex';
        } else {
            return 'curl -fsSL https://github.com/zhuqingyv/mnemo/releases/latest/download/install.sh | sh';
        }
    }

    static getPlatformDisplayName(os, arch) {
        const osNames = {
            'darwin': 'macOS',
            'linux': 'Linux',
            'windows': 'Windows'
        };

        const archNames = {
            'x86_64': 'Intel/AMD 64位',
            'arm64': 'ARM 64位'
        };

        return `${osNames[os] || os} ${archNames[arch] || arch}`;
    }
}

// GitHub API client
class GitHubAPI {
    constructor() {
        this.repo = 'zhuqingyv/mnemo';
        this.baseURL = `https://api.github.com/repos/${this.repo}`;
        this.cache = new Map();
        this.cacheTimeout = 5 * 60 * 1000; // 5 minutes
    }

    async fetchWithCache(url) {
        const cacheKey = url;
        const cached = this.cache.get(cacheKey);

        if (cached && Date.now() - cached.timestamp < this.cacheTimeout) {
            return cached.data;
        }

        try {
            const response = await fetch(url);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            const data = await response.json();
            this.cache.set(cacheKey, {
                data,
                timestamp: Date.now()
            });

            return data;
        } catch (error) {
            console.error('GitHub API error:', error);
            throw error;
        }
    }

    async getLatestRelease() {
        const url = `${this.baseURL}/releases/latest`;
        return this.fetchWithCache(url);
    }

    async getReleases(limit = 5) {
        const url = `${this.baseURL}/releases?per_page=${limit}`;
        return this.fetchWithCache(url);
    }

    async getRepoInfo() {
        const url = this.baseURL;
        return this.fetchWithCache(url);
    }
}

// Main application
class MnemoWebsite {
    constructor() {
        this.githubAPI = new GitHubAPI();
        this.platform = PlatformDetector.detect();
        this.currentRelease = null;

        this.init();
    }

    async init() {
        this.setupEventListeners();
        await this.loadReleaseData();
        this.updatePlatformInfo();
        this.setupCopyButtons();
    }

    setupEventListeners() {
        // Installation method tabs
        document.querySelectorAll('.method-card').forEach(card => {
            card.addEventListener('click', () => {
                document.querySelectorAll('.method-card').forEach(c => c.classList.remove('active'));
                card.classList.add('active');
            });
        });

        // Install button
        document.getElementById('install-btn').addEventListener('click', () => {
            this.handleInstall();
        });
    }

    async loadReleaseData() {
        try {
            this.showLoading();

            // Load latest release
            this.currentRelease = await this.githubAPI.getLatestRelease();

            // Update version info
            const versionElement = document.getElementById('latest-version');
            versionElement.textContent = this.currentRelease.tag_name;

            // Load repo info for download count
            const repoInfo = await this.githubAPI.getRepoInfo();
            const downloadsElement = document.getElementById('total-downloads');
            downloadsElement.textContent = this.formatNumber(repoInfo.stargazers_count); // Using stars as fallback

            // Generate download links
            this.generateDownloadLinks();

        } catch (error) {
            console.error('Failed to load release data:', error);
            this.showError('加载版本信息失败，请刷新页面重试');
        } finally {
            this.hideLoading();
        }
    }

    updatePlatformInfo() {
        const { os, arch } = this.platform;
        const platformElement = document.getElementById('detected-platform');
        const packageElement = document.getElementById('recommended-package');
        const installCommandElement = document.getElementById('install-command');

        // Update platform display
        const platformName = PlatformDetector.getPlatformDisplayName(os, arch);
        platformElement.textContent = platformName;

        // Update recommended package
        const packageName = PlatformDetector.getPackageName(os, arch);
        if (packageName) {
            packageElement.textContent = packageName;
        } else {
            packageElement.textContent = '不支持的平台';
            packageElement.style.color = '#ef4444';
        }

        // Update install command
        const installCommand = PlatformDetector.getInstallCommand(os);
        installCommandElement.textContent = installCommand;
    }

    generateDownloadLinks() {
        if (!this.currentRelease) return;

        const downloadList = document.getElementById('download-list');
        downloadList.innerHTML = '';

        // Define platform order
        const platforms = [
            { os: 'darwin', arch: 'arm64', name: 'macOS Apple Silicon' },
            { os: 'darwin', arch: 'x86_64', name: 'macOS Intel' },
            { os: 'linux', arch: 'x86_64', name: 'Linux x86_64' },
            { os: 'linux', arch: 'arm64', name: 'Linux ARM64' },
            { os: 'windows', arch: 'x86_64', name: 'Windows x86_64' },
            { os: 'windows', arch: 'arm64', name: 'Windows ARM64' }
        ];

        platforms.forEach(({ os, arch, name }) => {
            const packageName = PlatformDetector.getPackageName(os, arch);
            if (!packageName) return;

            const asset = this.currentRelease.assets.find(a => a.name === packageName);
            if (!asset) return;

            const link = document.createElement('a');
            link.href = asset.browser_download_url;
            link.className = 'download-link';
            link.innerHTML = `
                <span class="download-platform">${name}</span>
                <span class="download-size">${this.formatFileSize(asset.size)}</span>
            `;

            downloadList.appendChild(link);
        });

        // Add desktop apps
        const desktopAssets = this.currentRelease.assets.filter(a =>
            a.name.startsWith('mnemo-desktop-')
        );

        if (desktopAssets.length > 0) {
            const separator = document.createElement('div');
            separator.style.marginTop = '1rem';
            separator.style.paddingTop = '1rem';
            separator.style.borderTop = '1px solid #e2e8f0';
            separator.innerHTML = '<strong>桌面应用</strong>';
            downloadList.appendChild(separator);

            desktopAssets.forEach(asset => {
                const link = document.createElement('a');
                link.href = asset.browser_download_url;
                link.className = 'download-link';
                link.innerHTML = `
                    <span class="download-platform">${asset.name}</span>
                    <span class="download-size">${this.formatFileSize(asset.size)}</span>
                `;

                downloadList.appendChild(link);
            });
        }
    }

    setupCopyButtons() {
        document.querySelectorAll('.copy-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const targetId = btn.dataset.copy;
                const targetElement = document.getElementById(targetId);

                if (!targetElement) return;

                const text = targetElement.textContent;

                try {
                    await navigator.clipboard.writeText(text);

                    // Visual feedback
                    const originalText = btn.textContent;
                    btn.textContent = '已复制!';
                    btn.style.background = '#22c55e';

                    setTimeout(() => {
                        btn.textContent = originalText;
                        btn.style.background = '';
                    }, 2000);

                } catch (error) {
                    console.error('Failed to copy:', error);

                    // Fallback for older browsers
                    const textArea = document.createElement('textarea');
                    textArea.value = text;
                    textArea.style.position = 'fixed';
                    textArea.style.opacity = '0';
                    document.body.appendChild(textArea);
                    textArea.select();
                    document.execCommand('copy');
                    document.body.removeChild(textArea);

                    btn.textContent = '已复制!';
                    setTimeout(() => {
                        btn.textContent = '复制';
                    }, 2000);
                }
            });
        });
    }

    handleInstall() {
        const { os } = this.platform;
        const packageName = PlatformDetector.getPackageName(os, this.platform.arch);

        if (!packageName) {
            alert('抱歉，当前平台不受支持。请手动查看 GitHub Releases 页面。');
            return;
        }

        // Scroll to installation section
        document.getElementById('installation').scrollIntoView({
            behavior: 'smooth'
        });

        // Highlight the one-click install method
        const oneClickCard = document.getElementById('one-click-install');
        document.querySelectorAll('.method-card').forEach(c => c.classList.remove('active'));
        oneClickCard.classList.add('active');

        // Copy install command to clipboard
        const installCommand = PlatformDetector.getInstallCommand(os);
        navigator.clipboard.writeText(installCommand).then(() => {
            // Show success message
            this.showNotification('安装命令已复制到剪贴板！');
        });
    }

    showNotification(message) {
        // Create notification element
        const notification = document.createElement('div');
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: #22c55e;
            color: white;
            padding: 1rem 1.5rem;
            border-radius: 8px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.1);
            z-index: 10000;
            animation: slideIn 0.3s ease-out;
        `;
        notification.textContent = message;

        document.body.appendChild(notification);

        // Auto remove after 3 seconds
        setTimeout(() => {
            notification.style.animation = 'slideOut 0.3s ease-out';
            setTimeout(() => {
                document.body.removeChild(notification);
            }, 300);
        }, 3000);
    }

    showLoading() {
        document.querySelectorAll('.loading-placeholder').forEach(el => {
            el.classList.add('loading');
        });
    }

    hideLoading() {
        document.querySelectorAll('.loading-placeholder').forEach(el => {
            el.classList.remove('loading');
        });
    }

    showError(message) {
        const versionElement = document.getElementById('latest-version');
        versionElement.textContent = '加载失败';
        versionElement.style.color = '#ef4444';

        console.error(message);
    }

    formatNumber(num) {
        if (num >= 1000000) {
            return (num / 1000000).toFixed(1) + 'M';
        } else if (num >= 1000) {
            return (num / 1000).toFixed(1) + 'K';
        }
        return num.toString();
    }

    formatFileSize(bytes) {
        const sizes = ['B', 'KB', 'MB', 'GB'];
        if (bytes === 0) return '0 B';

        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return Math.round(bytes / Math.pow(1024, i) * 100) / 100 + ' ' + sizes[i];
    }
}

// Add CSS animations
const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from {
            transform: translateX(100%);
            opacity: 0;
        }
        to {
            transform: translateX(0);
            opacity: 1;
        }
    }

    @keyframes slideOut {
        from {
            transform: translateX(0);
            opacity: 1;
        }
        to {
            transform: translateX(100%);
            opacity: 0;
        }
    }
`;
document.head.appendChild(style);

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new MnemoWebsite();
});

// Add smooth scrolling for navigation links
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
        e.preventDefault();
        const target = document.querySelector(this.getAttribute('href'));
        if (target) {
            target.scrollIntoView({
                behavior: 'smooth',
                block: 'start'
            });
        }
    });
});
