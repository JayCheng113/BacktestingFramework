# OpenTrading Web

OpenTrading 的浏览器端应用，基于 React 19、TypeScript、Vite、Tailwind CSS 和 ECharts。

## 常用命令

```bash
npm install
npm run dev
npm test -- --run
npm run build
```

开发服务器默认监听 `http://localhost:3000`，并把 `/api` 代理到后端 `http://localhost:8000`。生产构建产物由后端容器或静态文件服务托管。

## 目录约定

- `src/pages/`：页面级工作流，如回测、因子、组合、模拟实盘
- `src/components/`：可复用组件和大型功能面板
- `src/api/`：后端 API 客户端与接口类型
- `src/types/`：跨页面共享的 TypeScript 类型

修改前端时同步运行 `npm test -- --run`；涉及 API 合约时同时检查后端路由和 `src/api/` 类型定义。
