import { Icon } from '../components/Icon'
import { site } from '../content/site'
import { useReveal } from '../hooks/useReveal'

const docNavigation = [
  { label: '开始了解', href: '#start' },
  { label: '工作方式', href: '#workflow-docs' },
  { label: 'AI 服务配置', href: '#provider' },
  { label: '隐私与数据', href: '#privacy' },
  { label: '离线能力包', href: '#offline' },
  { label: '发布边界', href: '#release' },
  { label: '常见问题', href: '#faq' },
]

export function DocsPage() {
  useReveal()

  return (
    <main className="subpage" id="main-content">
      <header className="subpage-hero">
        <div className="container" data-reveal>
          <span className="eyebrow">产品文档</span>
          <h1>先理解边界，再开始一场会议。</h1>
          <p>这里说明 Windows 公开预览版能做什么、如何导入离线能力包、怎样处理数据，以及可选 AI 分析服务如何接入。</p>
          <div className="subpage-hero__actions">
            <a className="button button--primary" href={site.windowsDownloadUrl}>
              下载 Windows 版 <Icon name="download" size={17} />
            </a>
            <a className="button button--outline" href="/#product-preview">
              查看界面预览
            </a>
          </div>
        </div>
      </header>

      <div className="container docs-layout">
        <aside className="docs-sidebar" data-reveal>
          <strong>本页内容</strong>
          <nav aria-label="文档目录">
            {docNavigation.map((item) => (
              <a key={item.href} href={item.href}>
                {item.label}
              </a>
            ))}
          </nav>
          <div className="docs-sidebar__status">
            <span className="status-dot" />
            <p>
              <strong>Windows 0.1.0</strong>
              未签名 · 公开预览
            </p>
          </div>
        </aside>

        <article className="docs-content">
          <section id="start" data-reveal>
            <span className="docs-kicker">01</span>
            <h2>当前产品是什么</h2>
            <p>
              Meeting Copilot 是面向中文技术会议的实时副驾驶。它维护当前议题和未闭环问题，在仍来得及追问时给出一条带证据的建议；会后保留连续文字、录音和复盘。
            </p>
            <div className="docs-callout docs-callout--info">
              <Icon name="info" size={20} />
              <p>
                <strong>当前阶段</strong>
                0.1.0 已作为 Windows 公开预览版交付，但尚未完成代码签名，不是承诺生产 SLA 的稳定版。
              </p>
            </div>
            <h3>适合优先验证的会议</h3>
            <div className="docs-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>场景</th>
                    <th>主要发现</th>
                    <th>当前优先级</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>API / 接口评审</td>
                    <td>Owner、验收、兼容性、回滚</td>
                    <td>P0</td>
                  </tr>
                  <tr>
                    <td>发布 / 灰度评审</td>
                    <td>比例、指标、暂停与回滚条件</td>
                    <td>P0</td>
                  </tr>
                  <tr>
                    <td>事故复盘</td>
                    <td>根因、缓解、告警与后续动作</td>
                    <td>P1</td>
                  </tr>
                  <tr>
                    <td>架构设计</td>
                    <td>候选方案、依赖、取舍与风险</td>
                    <td>P1</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section id="workflow-docs" data-reveal>
            <span className="docs-kicker">02</span>
            <h2>一次会议如何工作</h2>
            <ol className="docs-steps">
              <li>
                <span>1</span>
                <div>
                  <strong>用户手动开始</strong>
                  <p>请求麦克风权限，显示输入、录音、识别和 AI 分析状态。</p>
                </div>
              </li>
              <li>
                <span>2</span>
                <div>
                  <strong>本地优先识别</strong>
                  <p>实时语音形成暂定与确认文字，稳定内容进入证据链。</p>
                </div>
              </li>
              <li>
                <span>3</span>
                <div>
                  <strong>维护议题和问题</strong>
                  <p>工程语境足够时识别未闭环点，证据不足时保持待确认。</p>
                </div>
              </li>
              <li>
                <span>4</span>
                <div>
                  <strong>用户决定如何处理</strong>
                  <p>查看依据、保留、忽略或反馈，不由 AI 自动替团队执行。</p>
                </div>
              </li>
              <li>
                <span>5</span>
                <div>
                  <strong>结束并整理</strong>
                  <p>在同一场会议中回看复盘、未闭环项、完整文字与录音。</p>
                </div>
              </li>
            </ol>
          </section>

          <section id="provider" data-reveal>
            <span className="docs-kicker">03</span>
            <h2>OpenAI-compatible 分析服务</h2>
            <p>
              官网本身不会连接你的中转服务，也不会在浏览器内保存密钥。产品运行环境只有在显式配置后才启用远程分析；密钥必须保留在本地后端或桌面运行时中。
            </p>
            <div className="code-block" aria-label="LLM 环境变量示例">
              <div>
                <span>环境变量示例</span>
                <Icon name="code-2" size={17} />
              </div>
              <pre>
                <code>{`LLM_GATEWAY_BASE_URL=https://your-gateway.example/v1
LLM_GATEWAY_API_KEY=••••••••
LLM_GATEWAY_MODEL=your-model
LLM_GATEWAY_PROVIDER_LABEL=openai_compatible_gateway
LLM_GATEWAY_TIMEOUT_SECONDS=60
LLM_GATEWAY_IS_MOCK=false`}</code>
              </pre>
            </div>
            <div className="docs-callout docs-callout--warning">
              <Icon name="circle-alert" size={20} />
              <p>
                <strong>不要把密钥写进官网环境变量</strong>
                所有以 VITE_ 开头的值都会进入公开前端产物。真实 AI Demo 必须经过带鉴权、限流与预算控制的服务端代理。
              </p>
            </div>
          </section>

          <section id="privacy" data-reveal>
            <span className="docs-kicker">04</span>
            <h2>隐私与数据边界</h2>
            <div className="boundary-grid">
              <div>
                <Icon name="shield-check" size={22} />
                <h3>默认留在本地</h3>
                <p>会议文字、录音与任务优先由本地运行时处理和保存。</p>
              </div>
              <div>
                <Icon name="hand" size={22} />
                <h3>只有显式启用才调用</h3>
                <p>远程 LLM 不是默认免费能力，也不能被描述为完全离线。</p>
              </div>
              <div>
                <Icon name="activity" size={22} />
                <h3>状态不隐藏</h3>
                <p>录音、处理、保存、错误和恢复过程都需要明确显示。</p>
              </div>
              <div>
                <Icon name="database-backup" size={22} />
                <h3>删除语义可解释</h3>
                <p>删除会议应清理受控本地事实、录音与派生任务。</p>
              </div>
            </div>
          </section>

          <section id="offline" data-reveal>
            <span className="docs-kicker">05</span>
            <h2>导入完整离线 ASR 能力包</h2>
            <p>
              基础安装包保持轻量，约 3.05 GiB 的完整离线 ASR 能力包单独分发。下载后不要解压或重命名，在客户端“本地能力”页面直接导入 <code>.mcpkg</code> 文件。
            </p>
            <ol className="docs-steps">
              <li>
                <span>1</span>
                <div>
                  <strong>下载完整能力包</strong>
                  <p>从发布方提供的百度网盘或受控下载地址获取原始 <code>.mcpkg</code> 文件。</p>
                </div>
              </li>
              <li>
                <span>2</span>
                <div>
                  <strong>打开“本地能力”</strong>
                  <p>选择“导入离线包”，也可以把文件拖入导入区域。</p>
                </div>
              </li>
              <li>
                <span>3</span>
                <div>
                  <strong>等待自动校验和激活</strong>
                  <p>客户端会检查平台、磁盘空间、包清单、成员路径与文件哈希，再原子激活运行时。</p>
                </div>
              </li>
              <li>
                <span>4</span>
                <div>
                  <strong>按提示重启客户端</strong>
                  <p>重启后确认实时转写与录音转写均显示“已就绪”。</p>
                </div>
              </li>
            </ol>
          </section>

          <section id="release" data-reveal>
            <span className="docs-kicker">06</span>
            <h2>发布与下载边界</h2>
            <p>官网的下载按钮由独立发布清单驱动。当前只开放 Windows x64 基础安装包；macOS 尚需独立构建、Developer ID 签名与 Apple 公证，Web 应用尚未建立多用户隔离和公网安全边界。</p>
            <ul className="release-checklist">
              <li>
                <Icon name="circle-check" size={18} />
                <span>已开放：Windows 10/11 x64 基础安装包与 SHA-256 校验文件。</span>
              </li>
              <li>
                <Icon name="clock-3" size={18} />
                <span>进行中：Windows 代码签名、macOS 独立构建与公证、更多干净机器验收。</span>
              </li>
              <li>
                <Icon name="circle-alert" size={18} />
                <span>尚未开放：公网 Web 应用、macOS 安装包、企业级生产 SLA。</span>
              </li>
            </ul>
            <div className="docs-callout docs-callout--warning">
              <Icon name="circle-alert" size={20} />
              <p>
                <strong>当前 Windows 安装包未签名</strong>
                SmartScreen 可能显示提示。请只从官网下载安装包，并核对随版本发布的 SHA-256 校验文件。
              </p>
            </div>
          </section>

          <section id="faq" data-reveal>
            <span className="docs-kicker">07</span>
            <h2>常见问题</h2>
            <div className="faq-list">
              <details>
                <summary>为什么基础安装包不直接包含完整 ASR 模型？</summary>
                <p>完整能力包约 3.05 GiB。拆分后，客户端升级不需要重复下载模型；需要本地转写时，在客户端导入一次即可。</p>
              </details>
              <details>
                <summary>产品是不是完全离线？</summary>
                <p>不是。中文语音识别采用本地优先策略；分析能力可以连接用户显式配置的 OpenAI-compatible 远程服务。</p>
              </details>
              <details>
                <summary>macOS 和 Web 版什么时候开放？</summary>
                <p>macOS 需要在 Mac 上完成构建、Developer ID 签名、公证与安装验收。Web 版需要先增加账户、数据隔离、配额和公网安全设计，因此首发只提供 Windows 桌面版。</p>
              </details>
              <details>
                <summary>能否直接把模型中转地址写进官网？</summary>
                <p>公开地址可以，但密钥绝对不可以。浏览器端密钥无法保密，未来真实在线演示必须增加受控服务端代理。</p>
              </details>
              <details>
                <summary>什么时候需要给官网加后端和数据库？</summary>
                <p>只有开始保存内测线索、做账号、授权下载、支付或精确统计时才需要。当前内容使用 TypeScript 与 JSON 更轻、更易部署。</p>
              </details>
            </div>
          </section>
        </article>
      </div>
    </main>
  )
}
