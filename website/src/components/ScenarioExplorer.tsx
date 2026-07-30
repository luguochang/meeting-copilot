import { useState } from 'react'
import { scenarios, type Scenario } from '../content/site'
import { Icon } from './Icon'

export function ScenarioExplorer() {
  const [activeId, setActiveId] = useState<Scenario['id']>('architecture')
  const activeScenario = scenarios.find((scenario) => scenario.id === activeId) || scenarios[0]

  return (
    <div className="scenario-explorer" data-reveal>
      <div className="scenario-tabs" role="tablist" aria-label="技术会议场景">
        {scenarios.map((scenario) => (
          <button
            key={scenario.id}
            id={`scenario-tab-${scenario.id}`}
            role="tab"
            type="button"
            aria-selected={activeId === scenario.id}
            aria-controls="scenario-panel"
            className={activeId === scenario.id ? 'is-active' : ''}
            onClick={() => setActiveId(scenario.id)}
          >
            {scenario.label}
          </button>
        ))}
      </div>

      <div
        className="scenario-panel"
        id="scenario-panel"
        role="tabpanel"
        aria-labelledby={`scenario-tab-${activeScenario.id}`}
      >
        <div className="scenario-panel__copy">
          <span className="eyebrow">{activeScenario.label}</span>
          <h3>{activeScenario.title}</h3>
          <p>{activeScenario.description}</p>
          <ul>
            {activeScenario.gaps.map((gap) => (
              <li key={gap}>
                <Icon name="circle-check" size={17} />
                <span>{gap}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="scenario-evidence" key={activeScenario.id}>
          <div className="scenario-evidence__top">
            <span>
              <i /> 录音中
            </span>
            <div className="mini-wave" aria-hidden="true">
              {Array.from({ length: 18 }, (_, index) => (
                <i key={index} />
              ))}
            </div>
            <time>00:42:16</time>
          </div>
          <div className="scenario-evidence__question">
            <span>现在最值得追问</span>
            <strong>{activeScenario.question}</strong>
          </div>
          <div className="scenario-evidence__source">
            <Icon name="scan-search" size={18} />
            <p>
              <span>关联依据</span>
              {activeScenario.evidence}
            </p>
          </div>
          <div className="scenario-evidence__actions">
            <button type="button">查看依据</button>
            <button type="button">保留</button>
            <button type="button">忽略</button>
          </div>
        </div>
      </div>
    </div>
  )
}
