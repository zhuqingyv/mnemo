const REPO_API = "https://api.github.com/repos/zhuqingyv/mnemo/releases/latest";
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
    "darwin-arm64": "mnemo-darwin-arm64",
    "darwin-x86_64": "mnemo-darwin-x86_64",
    "linux-arm64": "mnemo-linux-arm64",
    "linux-x86_64": "mnemo-linux-x86_64",
    "windows-arm64": "mnemo-windows-arm64.exe",
    "windows-x86_64": "mnemo-windows-x86_64.exe",
  };

  return packages[`${platform.os}-${platform.arch}`] || t("viewGithubReleases");
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

function updateInstallCard() {
  const platform = detectPlatform();
  const detected = document.getElementById("detected-platform");
  const recommended = document.getElementById("recommended-package");
  const command = document.getElementById("install-command");
  const directDownload = document.getElementById("direct-download");
  const packageName = packageNameFor(platform);

  detected.textContent = platformLabel(platform);
  recommended.textContent = packageName;
  command.textContent = installCommand(platform);
  directDownload.href = `https://github.com/zhuqingyv/mnemo/releases/latest/download/${packageName}`;
  directDownload.textContent = `${t("downloadPrefix")} ${packageName}`;
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
    return;
  }

  try {
    const response = await fetch(REPO_API);
    if (!response.ok) {
      throw new Error("release fetch failed");
    }
    const release = await response.json();
    label.textContent = `${release.tag_name} · ${t("latestRelease")}`;
    version.textContent = release.tag_name;
    generated.textContent = release.published_at ? `${t("published")} ${release.published_at}` : t("latestGithubRelease");
  } catch {
    label.textContent = t("latestRelease");
    version.textContent = "unknown";
    generated.textContent = t("githubReleaseUnavailable");
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
