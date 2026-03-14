[phases.setup]
nixPkgs = [
  "python311",
  "nodejs_20",
  "chromium",
  "nss",
  "nspr",
  "atk",
  "cups",
  "libdrm",
  "dbus",
  "libxkbcommon",
  "xorg.libX11",
  "xorg.libXcomposite",
  "xorg.libXdamage",
  "xorg.libXext",
  "xorg.libXfixes",
  "xorg.libXrandr",
  "xorg.libxcb",
  "mesa",
  "expat",
  "libxshmfence",
  "glib",
  "gtk3",
  "pango",
  "cairo",
  "alsa-lib"
]

[phases.install]
cmds = [
  "pip install -r requirements.txt",
  "playwright install chromium",
  "cd frontend && npm install"
]

[phases.build]
cmds = [
  "cd frontend && npm run build",
  "cp -r frontend/out frontend_out"
]

[start]
cmd = "uvicorn api:app --host 0.0.0.0 --port $PORT"
