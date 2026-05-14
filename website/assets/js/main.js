const REPO_API = "https://api.github.com/repos/zhuqingyv/mnemo/releases/latest";
const RELEASE_PAGE = "https://github.com/zhuqingyv/mnemo/releases/latest";
let latestRelease = null;
let currentLanguage = window.MNEMO_I18N?.getInitialLanguage?.() || "zh-CN";

function t(key) {
  const dictionaries = window.MNEMO_I18N?.dictionaries || {};
  const dict = dictionaries[currentLanguage] || dictionaries["zh-CN"] || {};
  return dict[key] || key;
}

function applyTranslations() {
  document.documentElement.lang = currentLanguage;
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    const key = element.getAttribute("data-i18n");
    if (key) element.textContent = t(key);
  });
  document.querySelectorAll(".agent-card > span").forEach((element) => {
    const text = element.textContent || "";
    if (["已链接", "已連結", "Linked"].some((value) => text.includes(value))) {
      element.innerHTML = `<i></i>${t("linked")}`;
    }
    if (["已安装", "已安裝", "Installed"].some((value) => text.includes(value))) {
      element.innerHTML = `<i></i>${t("installed")}`;
    }
    if (["未安装", "未安裝", "Not installed"].some((value) => text.includes(value))) {
      element.innerHTML = `<i></i>${t("notInstalled")}`;
    }
  });
  document.querySelectorAll(".agent-card button").forEach((button) => {
    if (button.classList.contains("button-install")) {
      button.textContent = t("openInstall");
      return;
    }
    button.textContent = t("unlink");
  });
  updateInstallCard();
}

function detectPlatform() {
  const platform = navigator.platform || "";
  const userAgent = navigator.userAgent || "";
  const userAgentData = navigator.userAgentData;

  let os = "unknown";
  let arch = "x86_64";

  if (/Mac/i.test(platform) || /Mac OS/i.test(userAgent)) {
    os = "darwin";
  } else if (/Win/i.test(platform) || /Windows/i.test(userAgent)) {
    os = "windows";
  } else if (/Linux/i.test(platform) || /Linux/i.test(userAgent)) {
    os = "linux";
  }

  if (/arm64|aarch64/i.test(platform) || /arm64|aarch64/i.test(userAgent)) {
    arch = "arm64";
  }

  if (userAgentData?.platform === "macOS" && /arm/i.test(userAgentData.architecture || "")) {
    arch = "arm64";
  }

  return { os, arch };
}

function packageNameFor(platform) {
  const packages = {
    "darwin-arm64": "mnemo-desktop-macos-arm64.dmg",
    "darwin-x86_64": "mnemo-desktop-macos-x86_64.dmg",
    "linux-x86_64": "mnemo-desktop-linux-x86_64.AppImage",
    "windows-x86_64": "mnemo-desktop-windows-x86_64.exe",
  };

  return packages[`${platform.os}-${platform.arch}`] || "";
}

function platformLabel(platform) {
  const osLabels = {
    darwin: "macOS",
    linux: "Linux",
    windows: "Windows",
    unknown: "Unknown OS",
  };

  const archLabels = {
    arm64: "ARM64",
    x86_64: "Intel / AMD 64-bit",
  };

  return `${osLabels[platform.os] || platform.os} · ${archLabels[platform.arch] || platform.arch}`;
}

function installCommand(platform) {
  if (platform.os === "windows") {
    return "irm https://github.com/zhuqingyv/mnemo/releases/latest/download/install.ps1 | iex";
  }

  return "curl -fsSL https://github.com/zhuqingyv/mnemo/releases/latest/download/install.sh | sh";
}

function installNoteFor(platform, packageName) {
  if (!packageName) return t("installNoteUnavailable");
  if (platform.os === "darwin") return t("installNoteMac");
  if (platform.os === "windows") return t("installNoteWindows");
  if (platform.os === "linux") return t("installNoteLinux");
  return t("installNoteUnavailable");
}

function findReleaseAsset(release, packageName) {
  return release?.assets?.find((asset) => asset.name === packageName) || null;
}

function updateInstallCard() {
  const platform = detectPlatform();
  const detected = document.getElementById("detected-platform");
  const recommended = document.getElementById("recommended-package");
  const command = document.getElementById("install-command");
  const directDownload = document.getElementById("direct-download");
  const installNote = document.getElementById("install-note");
  const packageName = packageNameFor(platform);
  const asset = findReleaseAsset(latestRelease, packageName);

  detected.textContent = platformLabel(platform);
  command.textContent = installCommand(platform);
  if (!packageName) {
    recommended.textContent = t("viewGithubReleases");
    directDownload.href = RELEASE_PAGE;
    directDownload.textContent = t("viewGithubReleases");
    if (installNote) installNote.textContent = t("installNoteUnavailable");
  } else if (!latestRelease) {
    recommended.textContent = packageName;
    directDownload.href = RELEASE_PAGE;
    directDownload.textContent = t("detecting");
    if (installNote) installNote.textContent = installNoteFor(platform, packageName);
  } else if (asset?.browser_download_url) {
    recommended.textContent = packageName;
    directDownload.href = asset.browser_download_url;
    directDownload.textContent = `${t("downloadPrefix")} ${packageName}`;
    if (installNote) installNote.textContent = installNoteFor(platform, packageName);
  } else {
    recommended.textContent = t("installNoteUnavailable");
    directDownload.href = RELEASE_PAGE;
    directDownload.textContent = t("viewGithubReleases");
    if (installNote) installNote.textContent = t("installNoteUnavailable");
  }
}

async function updateReleaseLabel() {
  const label = document.getElementById("release-label");
  const version = document.getElementById("website-version");
  const generated = document.getElementById("website-generated");
  const releaseMetadata = window.MNEMO_RELEASE;

  if (releaseMetadata?.version && releaseMetadata.version !== "unknown" && releaseMetadata.version !== "dev") {
    label.textContent = `${releaseMetadata.version} · ${t("latestRelease")}`;
    version.textContent = releaseMetadata.version;
    generated.textContent = releaseMetadata.generatedAt
      ? `${t("deployed")} ${releaseMetadata.generatedAt}`
      : releaseMetadata.publishedAt
        ? `${t("published")} ${releaseMetadata.publishedAt}`
        : t("generatedByCi");
  }

  try {
    const response = await fetch(REPO_API);
    if (!response.ok) {
      throw new Error("release fetch failed");
    }
    const release = await response.json();
    latestRelease = release;
    label.textContent = `${release.tag_name} · ${t("latestRelease")}`;
    version.textContent = release.tag_name;
    generated.textContent = release.published_at ? `${t("published")} ${release.published_at}` : t("latestGithubRelease");
    updateInstallCard();
  } catch {
    if (!releaseMetadata?.version || releaseMetadata.version === "unknown" || releaseMetadata.version === "dev") {
      label.textContent = t("latestRelease");
      version.textContent = "unknown";
      generated.textContent = t("githubReleaseUnavailable");
    }
  }
}

function setupCopyButton() {
  const button = document.getElementById("copy-install");
  const command = document.getElementById("install-command");

  button.addEventListener("click", async () => {
    await navigator.clipboard.writeText(command.textContent || "");
    const originalText = button.textContent;
    button.textContent = t("copied");
    window.setTimeout(() => {
      button.textContent = originalText;
    }, 1400);
  });
}

updateInstallCard();
applyTranslations();
updateReleaseLabel();
setupCopyButton();

const languageSelect = document.getElementById("language-select");
if (languageSelect) {
  languageSelect.value = currentLanguage;
  languageSelect.addEventListener("change", () => {
    currentLanguage = window.MNEMO_I18N.normalizeLanguage(languageSelect.value);
    localStorage.setItem("mnemo_lang", currentLanguage);
    applyTranslations();
    updateReleaseLabel();
  });
}
