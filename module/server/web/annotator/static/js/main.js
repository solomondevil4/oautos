(function () {
  const STATIC_PREFIX = "/tool/annotator/static";

  // 版本号来自 index.html 里 main.js 的 ?v=，改前端后只需改那一处，
  // 这里自动跟上，避免浏览器继续用缓存的旧 css/js。
  const ASSET_VER = (function () {
    const self = document.currentScript;
    if (!self || !self.src) {
      return "";
    }
    try {
      return new URL(self.src, window.location.href).searchParams.get("v") || "";
    } catch (error) {
      return "";
    }
  })();

  function assetUrl(path) {
    return ASSET_VER ? `${STATIC_PREFIX}/${path}?v=${encodeURIComponent(ASSET_VER)}` : `${STATIC_PREFIX}/${path}`;
  }

  console.info("[annotator] 前端资源版本:", ASSET_VER || "(未设置)");

  const widgets = [
    { mountId: "topbarMount", name: "topbar", script: "topbar.js" },
    { mountId: "leftWindowMount", name: "left-window", script: "left-window.js" },
    { mountId: "centerPanelMount", name: "center-panel", script: "center-panel.js" },
    { mountId: "rightWindowMount", name: "right-window", script: "right-window.js" },
  ];

  function ensureRegistry() {
    if (!window.OASAnnotatorWidgets) {
      window.OASAnnotatorWidgets = {};
    }
    return window.OASAnnotatorWidgets;
  }

  async function fetchWidget(name) {
    const response = await fetch(assetUrl(`widget/${name}.html`), { cache: "no-cache" });
    if (!response.ok) {
      throw new Error(`加载组件失败: ${name} (${response.status})`);
    }
    return response.text();
  }

  async function mountWidgets() {
    for (const widget of widgets) {
      const mount = document.getElementById(widget.mountId);
      if (!mount) {
        throw new Error(`缺少组件挂载点: ${widget.mountId}`);
      }
      mount.innerHTML = await fetchWidget(widget.name);
    }
  }

  function loadScript(path) {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = path;
      script.async = false;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error(`脚本加载失败: ${path}`));
      document.body.appendChild(script);
    });
  }

  async function loadComponentScripts() {
    for (const widget of widgets) {
      await loadScript(assetUrl(`js/${widget.script}`));
    }

    const registry = ensureRegistry();
    for (const widget of widgets) {
      const component = registry[widget.name];
      if (component && typeof component.mount === "function") {
        component.mount();
      }
    }
  }

  async function loadRuntimeScripts() {
    // 保持原有初始化顺序：先布局脚本，再业务脚本。
    await loadScript(assetUrl("js/layout-ui.js"));
    await loadScript(assetUrl("js/app.js"));
  }

  function showBootError(error) {
    console.error(error);
    const mount = document.createElement("pre");
    mount.className = "small-output";
    mount.textContent = `页面初始化失败: ${error.message || String(error)}`;
    document.body.appendChild(mount);
  }

  async function bootstrap() {
    ensureRegistry();
    await mountWidgets();
    await loadComponentScripts();
    await loadRuntimeScripts();
  }

  bootstrap().catch(showBootError);
})();
