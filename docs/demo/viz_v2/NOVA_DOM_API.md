# nova-dom API 参考文档

> 包：`nova-view`（npm） / 仓库：[zhuqingyv/nova-dom](https://github.com/zhuqingyv/nova-dom)
> 当前版本：`0.1.18`
> 完整文档站：https://zhuqingyv.github.io/nova-dom/

一句话定位：**纯运行时、无 Virtual DOM 的响应式前端库**。链式 DOM API + Signal 响应式系统，没有 JSX 编译步骤，CDN 零构建直用。

核心理念：把 UI 写回 DOM，把更新交给 Signal，把产能交给 AI。

---

## 目录

1. [快速开始](#快速开始)
2. [响应式原语](#响应式原语)
3. [Dom 元素创建](#dom-元素创建)
4. [组件系统](#组件系统)
5. [控制流组件](#控制流组件)
6. [生命周期钩子](#生命周期钩子)
7. [上下文（Context）](#上下文context)
8. [挂载](#挂载)
9. [VirtualList 大数据量渲染](#virtuallist-大数据量渲染)
10. [ErrorBoundary 错误边界](#errorboundary-错误边界)
11. [类型定义速查](#类型定义速查)

---

## 快速开始

### 方式 A：UMD script tag（零构建）

```html
<!doctype html>
<html>
  <body>
    <div id="app"></div>
    <script src="https://unpkg.com/nova-view@0.1.18/dist/lib/nova-dom.umd.min.js"></script>
    <script>
      const { Dom, ref, mount } = NovaDom;   // UMD 全局挂载为 NovaDom

      const count = ref(0);
      const app = Dom.div()
        .class('app')(
          Dom.h1()('Hello Nova'),
          Dom.button().onClick(() => count.value++)(
            () => `clicked ${count.value} times`
          ),
        );

      mount(app, document.getElementById('app'));
    </script>
  </body>
</html>
```

> UMD 全局名是 `NovaDom`。所有导出的 API 都挂在该命名空间下。

### 方式 B：ESM import（Vite / Webpack / TS 项目）

```bash
npm install nova-view
```

```ts
import { Dom, ref, computed, mount } from 'nova-view';

const count = ref(0);
const doubled = computed(() => count.value * 2);

const App = Dom.div().class('app')(
  Dom.p()(() => `count = ${count.value}`),
  Dom.p()(() => `doubled = ${doubled.value}`),
  Dom.button().onClick(() => count.value++)('+1'),
);

mount(App, document.body);
```

> `package.json` 的 `main` 指向 `dist/lib/nova-dom.umd.min.js`、`module` 指向 `dist/lib/nova-dom.min.js`，打包器会自动选择 ESM 版本。

---

## 响应式原语

nova-dom 的响应式系统与 Vue 3 Composition API 形状类似：基础单位是 `Ref`，对象用 `reactive` 深层代理，副作用用 `effect` / `watch` / `watchEffect` 订阅。

### `ref<T>(value: T): Ref<T>`

创建一个响应式引用。通过 `.value` 读写。

```ts
import { ref } from 'nova-view';

const count = ref(0);
console.log(count.value);   // 0
count.value++;              // 触发订阅者重新执行
```

### `reactive<T extends object>(target: T): T`

把对象包成深层响应式代理，属性读写自动建立依赖追踪。

```ts
import { reactive } from 'nova-view';

const state = reactive({ user: { name: 'Alice' }, list: [] });
state.user.name = 'Bob';    // 嵌套属性写入也会触发更新
state.list.push(1);         // 数组操作也能被追踪
```

### `computed<T>(getter: () => T): Ref<T>`

派生响应式值，带缓存：依赖不变就不重新计算。返回一个只读 `Ref`，用 `.value` 读。

```ts
import { ref, computed } from 'nova-view';

const price = ref(10);
const qty = ref(3);
const total = computed(() => price.value * qty.value);
console.log(total.value);   // 30
```

### `watch<T>(source, cb, options?): () => void`

显式监听某个 getter 的返回值，变化时调用 `cb(newValue, oldValue)`。返回 stop 函数。

**签名：**
```ts
watch<T>(
  source: () => T,
  cb: (newVal: T, oldVal: T) => void,
  opts?: { immediate?: boolean; flush?: 'pre' | 'post' | 'sync' }
): () => void
```

```ts
import { ref, watch } from 'nova-view';

const name = ref('Alice');
const stop = watch(
  () => name.value,
  (n, o) => console.log(`${o} -> ${n}`),
  { immediate: true },
);
name.value = 'Bob';    // 输出 Alice -> Bob
stop();                // 取消监听
```

### `watchEffect(fn, opts?): () => void`

自动追踪 `fn` 内读取到的所有响应式依赖，任一变化都会重跑 `fn`。返回 stop 函数。

```ts
import { ref, watchEffect } from 'nova-view';

const a = ref(1), b = ref(2);
const stop = watchEffect(() => {
  console.log('sum =', a.value + b.value);
});
a.value = 10;   // 自动重跑
stop();
```

### `effect(fn, options?): ReactiveEffect`

底层副作用注册。大多数场景用 `watchEffect` 即可，`effect` 暴露更底层的 `scheduler` / `lazy` 选项。

```ts
import { effect, ref } from 'nova-view';

const x = ref(0);
const runner = effect(() => console.log(x.value), {
  scheduler: (run) => queueMicrotask(run),   // 自定义调度
  lazy: false,
});
```

### `batch<T>(fn: () => T): T`

把一组写操作合并成一个更新批次，批次结束统一 flush 订阅者。

```ts
import { ref, batch } from 'nova-view';

const a = ref(0), b = ref(0);
batch(() => {
  a.value = 1;
  b.value = 2;
});   // 订阅者只触发一次
```

### `toRef<T, K extends keyof T>(obj: T, key: K): Ref<T[K]>`

从响应式对象中抽出一个属性，返回一个同步保持的 `Ref`。改 `Ref` 也改原对象。

```ts
import { reactive, toRef } from 'nova-view';

const state = reactive({ count: 0 });
const countRef = toRef(state, 'count');
countRef.value++;            // state.count 也变成 1
```

### `toRefs<T>(obj: T): { [K in keyof T]: Ref<T[K]> }`

把响应式对象每个属性都转成 `Ref`，便于解构传参而保持响应性。

```ts
import { reactive, toRefs } from 'nova-view';

const state = reactive({ x: 1, y: 2 });
const { x, y } = toRefs(state);
x.value = 100;   // state.x 也是 100
```

### `read<T>(v: MaybeRef<T>): T`

把 `MaybeRef<T>` 读成普通 `T`。是 `Ref` 就取 `.value`，否则原样返回。写接收 `MaybeRef` 的工具函数时用得上。

```ts
import { ref, read } from 'nova-view';

function log(v: MaybeRef<string>) {
  console.log(read(v));
}
log('hello');       // 字面量
log(ref('world'));  // Ref
```

### `isRef<T>(r: unknown): r is Ref<T>`

类型守卫：判断是不是 `Ref`。

```ts
import { ref, isRef } from 'nova-view';

const a = ref(1);
if (isRef(a)) a.value++;
```

### `useSignal` / `getCurrentInstance`

`useSignal` 是一个低层 signal 工具；`getCurrentInstance` 在 `component()` 内拿到当前组件实例。具体签名查官方文档，常规业务用不到。

---

## Dom 元素创建

nova-dom **不用 JSX**，直接用 `Dom` 代理创建真实 DOM 元素，通过链式调用设置属性、事件、样式，再用**第二对括号**追加子元素。

### `Dom.<tag>()` → 元素代理

`Dom` 是一个 Proxy。访问任意 tag 名返回一个创建函数，调用后得到一个 `DomCallable` 元素代理（封装一个真实 `HTMLElement`）。

```ts
import { Dom } from 'nova-view';

const el = Dom.div();              // 创建 <div>
const img = Dom.img();             // 创建 <img>
const custom = Dom['my-card']();   // 创建自定义元素
```

### 链式属性：双括号模式

**第一对括号**：创建元素。
**链式 `.method(...)`**：设置属性 / 样式 / 事件，返回同一个代理，可继续链式。
**第二对括号**：追加子元素（children）。

```ts
import { Dom, ref } from 'nova-view';

const title = ref('Hello');

const card = Dom.div()
  .class('card card--primary')
  .style({ padding: '12px', background: '#fff' })
  .onClick((e) => console.log('clicked', e))(
    Dom.h2()(() => title.value),           // 子元素：动态文本
    Dom.p()('a static paragraph'),         // 子元素：静态文本
  );
```

### 支持的链式方法

通过 Proxy 动态拦截，**任意方法名都可用**，规则：

| 方法名模式 | 作用 | 示例 |
| --- | --- | --- |
| `.class(value)` | 设置 className | `.class('foo bar')` 或 `.class(() => active.value ? 'on' : '')` |
| `.style(value)` | 设置内联样式 | `.style({ color: 'red', marginTop: '10px' })` |
| `.on<Event>(handler)` | 绑定事件（`on` 前缀） | `.onClick(fn)` / `.onInput(fn)` / `.onMouseEnter(fn)` |
| `.<attr>(value)` | 设置 DOM 属性 / attribute | `.id('app')` / `.href('/home')` / `.disabled(true)` |

**值类型支持：**

- **字面量**：直接赋值
- **函数 `() => T`**：每次依赖变化重算并更新
- **`Ref<T>`**：自动追踪 `.value` 变化

```ts
const count = ref(0);
const disabled = computed(() => count.value >= 10);

Dom.button()
  .disabled(disabled)                      // 传 Ref，自动响应
  .class(() => count.value > 5 ? 'hot' : 'cool')  // 传函数，自动响应
  .style({ color: 'red' })                 // 静态对象
  .onClick(() => count.value++)(
    () => `count = ${count.value}`,
  );
```

**样式 key 会自动把驼峰转 kebab-case**：`marginTop` → `margin-top`。

### 追加子元素：第二对括号

子元素可传入的类型：

- 文本字符串 / 数字
- 另一个 `DomCallable` 元素
- 函数 `() => child`（响应式，内部用 `effect` 追踪）
- `Ref`（自动解包 `.value`）
- 数组（混合以上类型）

```ts
Dom.ul()(
  Dom.li()('static item'),
  () => items.value.map(item => Dom.li()(item.name)),  // 动态列表
  [Dom.li()('a'), Dom.li()('b')],                      // 数组也行
);
```

> 连续的文本子节点会用 nodePool 缓冲合并，减少 DOM 操作。但**列表渲染建议用 `For` 组件**，有 key diff 和 LIS 移动优化。

---

## 组件系统

### `component<P>(setup: (props: P) => Node): (props: P) => Node`

定义一个可复用组件。`setup` 在创建时执行一次，返回要渲染的 DOM 节点（原生 `Node`，因为没有 VDOM）。

**签名示意：**
```ts
component<P extends object>(
  setup: (props: P & { slots?: Slots; emit?: Emit; expose?: Expose }) => Node | Promise<Node>
): (props: P) => Node
```

**props 特性：**
- `props` 是只读的（`ReadonlyProps`），想让父组件改值要用 `emit`。
- 父组件传入的函数 / Ref 会保持响应性。

### 最简组件示例

```ts
import { component, ref, Dom } from 'nova-view';

const Counter = component<{ initial?: number }>((props) => {
  const count = ref(props.initial ?? 0);
  return Dom.div()(
    Dom.span()(() => `count: ${count.value}`),
    Dom.button().onClick(() => count.value++)('+'),
  );
});

// 使用：
mount(Counter({ initial: 10 }), document.body);
```

### Slots（插槽）

父组件通过 `slots` 传入具名 / 默认插槽；子组件在 setup 里取用。

```ts
const Card = component<{ slots: Slots }>(({ slots }) => {
  return Dom.div().class('card')(
    Dom.header()(slots.header?.()),    // 具名插槽
    Dom.main()(slots.default?.()),     // 默认插槽
    Dom.footer()(slots.footer?.()),
  );
});

mount(
  Card({
    slots: {
      header: () => Dom.h1()('Title'),
      default: () => Dom.p()('body content'),
      footer: () => Dom.small()('© 2026'),
    },
  }),
  document.body,
);
```

**作用域插槽**：插槽函数可接收参数。

```ts
const List = component<{ items: string[]; slots: Slots }>(({ items, slots }) => {
  return Dom.ul()(
    items.map((item, i) => Dom.li()(slots.item?.(item, i))),
  );
});

List({
  items: ['a', 'b'],
  slots: { item: (name, idx) => `${idx}: ${name}` },
});
```

### Emit（自定义事件）

子组件通过 `emit` 触发事件，父组件用 `onXxx` prop 接收。

```ts
const Child = component<{ emit: Emit }>(({ emit }) => {
  return Dom.button().onClick(() => emit('submit', { ok: true }))('submit');
});

const Parent = () => Child({
  emit: (name, payload) => {
    if (name === 'submit') console.log('got', payload);
  },
});
```

### Expose（暴露实例方法）

子组件可以把内部方法通过 `expose` 暴露给父组件。

```ts
const Modal = component<{ expose: Expose }>(({ expose }) => {
  const visible = ref(false);
  expose({
    open: () => (visible.value = true),
    close: () => (visible.value = false),
  });
  return Show(() => visible.value, {
    when: () => Dom.div()('I am a modal'),
  });
});
```

---

## 控制流组件

### `Show(cond, opts): Node`

条件渲染。

**签名：**
```ts
Show(
  cond: () => unknown,
  opts: {
    when: () => Node;
    fallback?: () => Node;
    keepAlive?: boolean;      // true: 两个分支都挂载，切换靠 display
    displayToggle?: boolean;  // true: 用 style.display 切换（隐式 keepAlive）
  }
): Node
```

```ts
import { Show, ref, Dom } from 'nova-view';

const loggedIn = ref(false);

const view = Show(() => loggedIn.value, {
  when: () => Dom.div()('Welcome back!'),
  fallback: () => Dom.button().onClick(() => loggedIn.value = true)('Login'),
  keepAlive: false,    // 默认 false：不满足时真的卸载
});
```

> `keepAlive: true` 的分支在第一次初始化时就挂载，切换只改 `display`，适合需要保留内部状态的 tab 场景。

### `For<T>(getList, opts): Node`

列表渲染，带 key diff 和 LIS 算法最小化 DOM 移动。

**签名：**
```ts
For<T>(
  getList: () => T[],
  opts: {
    key?: (item: T, index: number) => unknown;
    children: (item: T, index: number) => Node;
    fallback?: () => Node;              // 空列表时渲染
    optimizeMove?: boolean;             // 启用 LIS 移动优化
    controller?: (ctrl: ForController<T>) => void;   // 拿到命令式控制器
  }
): Node
```

```ts
import { For, ref, Dom } from 'nova-view';

const list = ref([{ id: 1, name: 'a' }, { id: 2, name: 'b' }]);

const view = For(() => list.value, {
  key: (item) => item.id,
  children: (item, i) => Dom.li()(() => `${i}: ${item.name}`),
  fallback: () => Dom.p()('(empty)'),
  optimizeMove: true,
});
```

**ForController（命令式操作列表）：**

拿到 controller 后可以跳过 diff 直接增删改，适合超高频更新场景。

```ts
let ctrl: ForController<Item>;
For(() => items.value, {
  key: i => i.id,
  children: i => Dom.li()(i.name),
  controller: c => (ctrl = c),
});

// 之后:
ctrl.insertItem(newItem, 0);
ctrl.updateItem(id, partial);
ctrl.removeItem(id);
ctrl.replaceAll(newList);
```

### `Switch<T>(disc, cases): Node`

多分支条件渲染。顺序匹配，第一个为真的 case 渲染。

**签名：**
```ts
Switch<T>(
  disc: () => T,
  cases: Array<[cond: (() => boolean) | boolean, render: () => Node]>
): Node
```

```ts
import { Switch, ref, Dom } from 'nova-view';

const status = ref<'loading' | 'ok' | 'error'>('loading');

const view = Switch(() => status.value, [
  [() => status.value === 'loading', () => Dom.p()('Loading...')],
  [() => status.value === 'ok',      () => Dom.p()('Done!')],
  [true,                             () => Dom.p()('Oops')],    // 默认分支
]);
```

---

## 生命周期钩子

所有钩子都**必须在 `component()` 的 setup 内同步调用**，用于注册回调到当前组件实例。

| 钩子 | 触发时机 |
| --- | --- |
| `onMounted(fn)` | 节点插入到 DOM 后 |
| `onUnmounted(fn)` | 节点从 DOM 卸载后 |
| `onBeforeUpdate(fn)` | 响应式依赖变化、即将更新前 |
| `onUpdated(fn)` | 更新完成后 |
| `onCleanup(fn)` | 副作用清理（watch / effect 重跑前） |
| `onErrorCaptured(fn)` | 子组件抛错时捕获 |
| `onActivated(fn)` | `Show` 的 keepAlive 分支激活时 |
| `onDeactivated(fn)` | `Show` 的 keepAlive 分支失活时 |

```ts
import { component, ref, onMounted, onUnmounted, onUpdated, Dom } from 'nova-view';

const Clock = component(() => {
  const now = ref(Date.now());
  let timer: any;

  onMounted(() => {
    timer = setInterval(() => (now.value = Date.now()), 1000);
  });
  onUnmounted(() => clearInterval(timer));
  onUpdated(() => console.log('clock rerendered'));

  return Dom.time()(() => new Date(now.value).toISOString());
});
```

**`onErrorCaptured`** 特别适合组件库内部防御式处理，返回 `false` 可阻止错误继续向上冒泡（`ErrorBoundary` 就是基于它实现的）。

---

## 上下文（Context）

类似 Vue 的 provide / inject：祖先 `provide` 一个值，后代任意深度可以 `inject` 到。

### `createContext<T>(defaultValue?: T): Context<T>`

```ts
import { createContext } from 'nova-view';

const ThemeContext = createContext<'light' | 'dark'>('light');
```

### `provide(context, value)` / `inject(context)`

```ts
import { component, provide, inject, Dom } from 'nova-view';

const Provider = component(({ slots }) => {
  provide(ThemeContext, 'dark');
  return Dom.div()(slots.default?.());
});

const Consumer = component(() => {
  const theme = inject(ThemeContext);   // 'dark'
  return Dom.p()(`theme: ${theme}`);
});

mount(
  Provider({ slots: { default: () => Consumer({}) } }),
  document.body,
);
```

> `inject` 拿到的是 provide 时的值；如果想让 consumer 自动响应 provider 内的状态变化，provide 一个 `Ref` / `reactive` 对象。

---

## 挂载

### `mount(node: Node, target: Element): () => void`

把一个 nova-dom 节点挂到真实 DOM 容器。返回 unmount 函数。

```ts
import { mount, Dom } from 'nova-view';

const app = Dom.div()('Hello');
const unmount = mount(app, document.getElementById('app')!);

// 需要销毁时：
unmount();
```

> `mount` 会触发挂载子树里的所有 `onMounted`；调用 unmount 会触发 `onUnmounted`。

---

## VirtualList 大数据量渲染

**签名（props）：**
```ts
type VirtualListProps<T> = {
  items: MaybeRef<T[]>;                                   // 数据
  height: number;                                         // 视口高度 px
  itemHeight?: number | ((item: T, index: number) => number);  // 行高
  overscan?: number;                                      // 可视区外多渲染几行
  key?: (item: T, index: number) => unknown;
  children: (item: T, index: number) => Node;             // 每行渲染函数
  onReachEnd?: () => void;                                // 滚动到底部回调
  threshold?: number;                                     // 触发 onReachEnd 的距离 px
};
```

支持**固定行高**和**动态行高**（通过 `ResizeObserver` 自动测量），内部用二分搜索定位可视区。

```ts
import { VirtualList, ref, Dom } from 'nova-view';

const items = ref(Array.from({ length: 100000 }, (_, i) => ({ id: i, label: `row ${i}` })));

const view = VirtualList<{ id: number; label: string }>({
  items,
  height: 600,
  itemHeight: 32,
  overscan: 5,
  key: (it) => it.id,
  children: (it, i) => Dom.div()
    .class('row')
    .style({ height: '32px' })(`${i}: ${it.label}`),
  onReachEnd: () => console.log('load more'),
  threshold: 200,
});

mount(view, document.body);
```

---

## ErrorBoundary 错误边界

**签名（props）：**
```ts
type ErrorBoundaryProps = {
  children: () => Node;
  fallback?: (error: Error, reset: () => void) => Node;
  onError?: (error: Error) => void;
};
```

内部基于 `onErrorCaptured` 钩子：捕获到错误后渲染 `fallback`（或默认红色错误面板），`reset()` 清空错误状态重新渲染 `children`。

```ts
import { ErrorBoundary, component, Dom } from 'nova-view';

const Broken = component(() => {
  throw new Error('boom!');
  return Dom.div()();
});

const view = ErrorBoundary({
  children: () => Broken({}),
  fallback: (err, reset) => Dom.div().class('err')(
    Dom.p()(err.message),
    Dom.button().onClick(reset)('retry'),
  ),
  onError: (err) => console.error('captured:', err),
});

mount(view, document.body);
```

---

## 类型定义速查

| 类型 | 含义 |
| --- | --- |
| `Ref<T>` | 带 `.value` 的响应式引用 |
| `Getter<T>` | `() => T` 形状的 getter |
| `MaybeRef<T>` | `T \| Ref<T>`，用 `read()` 解包 |
| `Slots` | 组件插槽字典，形如 `{ [name]: (...args) => Node }` |
| `Emit` | `(event: string, ...args: any[]) => void` |
| `Expose` | `(api: Record<string, any>) => void`，暴露实例方法 |
| `ReadonlyProps<P>` | 子组件 `props` 的只读包装 |
| `DomCallable` | `Dom.<tag>()` 返回的元素代理类型 |

---

## 参考源码位置

这份文档基于以下源码撰写，深入机制时可直接看对应文件：

| 模块 | 路径 |
| --- | --- |
| 公共 API barrel | `src/api.ts` → `src/index.ts` |
| 响应式核心 | `src/Base/Signal/runtime.ts` |
| 控制流（Show / For / Switch / mount） | `src/Base/Signal/controls.ts` |
| hook 代理（链式组件调用） | `src/Base/Signal/hook.ts` |
| 类型 | `src/Base/Signal/types.ts` |
| Dom 代理入口 | `src/Base/Dom/index.ts` |
| DomElement 链式实现 | `src/Base/Dom/DomElement.ts` |
| VirtualList | `src/components/VirtualList.ts` |
| ErrorBoundary | `src/components/ErrorBoundary.ts` |

---

## 使用提示

1. **无 VDOM**：`Dom.xxx()` 返回的是**真实 DOM 节点**的代理。可以直接 `el.appendChild()` 或 `el.querySelector()`（通过 `.el` 或直接传给原生 API）。
2. **响应性边界**：响应式更新只在子元素/属性的**函数或 Ref 形式**下生效。字面量传入之后不再自动更新。
3. **列表用 `For`，不要用 `items.map()`**：前者走 key diff + LIS，后者每次重建整个列表。
4. **`batch` 内部写多值**：避免中间状态触发多次渲染。
5. **CDN 直用的全局名是 `NovaDom`**，而不是 `NovaView` 或 `nova-dom`。
