import { useState } from 'react'
import { workflowSteps } from '../content/site'
import { Icon } from './Icon'

export function WorkflowOrbit() {
  const [activeId, setActiveId] = useState(workflowSteps[0].id)
  const activeIndex = workflowSteps.findIndex((step) => step.id === activeId)
  const activeStep = workflowSteps[activeIndex]

  return (
    <div className="workflow-orbit" data-reveal>
      <div className="workflow-orbit__visual" aria-label="会议闭环五个步骤">
        <div className="workflow-orbit__ring" aria-hidden="true" />
        <div className="workflow-orbit__core" aria-live="polite">
          <img src="/brand/talktrace-mark.svg" width="52" height="52" alt="" />
          <span>0{activeIndex + 1} / 05</span>
          <strong>{activeStep.shortLabel}</strong>
        </div>
        {workflowSteps.map((step, index) => (
          <button
            key={step.id}
            className={`workflow-node workflow-node--${index + 1}${activeId === step.id ? ' is-active' : ''}`}
            type="button"
            aria-pressed={activeId === step.id}
            aria-label={`查看步骤 ${index + 1}：${step.shortLabel}`}
            onClick={() => setActiveId(step.id)}
          >
            <span className="workflow-node__icon">
              <Icon name={step.icon} size={21} />
            </span>
            <span className="workflow-node__label">{step.shortLabel}</span>
          </button>
        ))}
      </div>

      <div className="workflow-orbit__detail" aria-live="polite">
        <span className="eyebrow">步骤 0{activeIndex + 1}</span>
        <h3>{activeStep.title}</h3>
        <p>{activeStep.description}</p>
        <div className="workflow-proof">
          <Icon name="circle-check" size={18} />
          <span>{activeStep.proof}</span>
        </div>
        <div className="workflow-orbit__controls" aria-label="切换工作步骤">
          {workflowSteps.map((step) => (
            <button
              key={step.id}
              type="button"
              className={activeId === step.id ? 'is-active' : ''}
              aria-label={`切换到${step.shortLabel}`}
              onClick={() => setActiveId(step.id)}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
