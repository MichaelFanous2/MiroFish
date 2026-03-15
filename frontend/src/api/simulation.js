import service, { requestWithRetry } from './index'

/**
 * 创建模拟
 * @param {Object} data - { project_id, graph_id?, enable_twitter?, enable_reddit? }
 */
export const createSimulation = (data) => {
  return requestWithRetry(() => service.post('/api/simulation/create', data), 3, 1000)
}

/**
 * 准备模拟环境（异步任务）
 * @param {Object} data - { simulation_id, entity_types?, use_llm_for_profiles?, parallel_profile_count?, force_regenerate? }
 */
export const prepareSimulation = (data) => {
  return requestWithRetry(() => service.post('/api/simulation/prepare', data), 3, 1000)
}

/**
 * 查询准备任务进度
 * @param {Object} data - { task_id?, simulation_id? }
 */
export const getPrepareStatus = (data) => {
  return service.post('/api/simulation/prepare/status', data)
}

/**
 * 获取模拟状态
 * @param {string} simulationId
 */
export const getSimulation = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}`)
}

/**
 * 获取模拟的 Agent Profiles
 * @param {string} simulationId
 * @param {string} platform - 'reddit' | 'twitter'
 */
export const getSimulationProfiles = (simulationId, platform = 'reddit') => {
  return service.get(`/api/simulation/${simulationId}/profiles`, { params: { platform } })
}

/**
 * 实时获取生成中的 Agent Profiles
 * @param {string} simulationId
 * @param {string} platform - 'reddit' | 'twitter'
 */
export const getSimulationProfilesRealtime = (simulationId, platform = 'reddit') => {
  return service.get(`/api/simulation/${simulationId}/profiles/realtime`, { params: { platform } })
}

/**
 * 获取模拟配置
 * @param {string} simulationId
 */
export const getSimulationConfig = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/config`)
}

/**
 * 实时获取生成中的模拟配置
 * @param {string} simulationId
 * @returns {Promise} 返回配置信息，包含元数据和配置内容
 */
export const getSimulationConfigRealtime = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/config/realtime`)
}

/**
 * 列出所有模拟
 * @param {string} projectId - 可选，按项目ID过滤
 */
export const listSimulations = (projectId) => {
  const params = projectId ? { project_id: projectId } : {}
  return service.get('/api/simulation/list', { params })
}

/**
 * 启动模拟
 * @param {Object} data - { simulation_id, platform?, max_rounds?, enable_graph_memory_update? }
 */
export const startSimulation = (data) => {
  return requestWithRetry(() => service.post('/api/simulation/start', data), 3, 1000)
}

/**
 * 停止模拟
 * @param {Object} data - { simulation_id }
 */
export const stopSimulation = (data) => {
  return service.post('/api/simulation/stop', data)
}

/**
 * 获取模拟运行实时状态
 * @param {string} simulationId
 */
export const getRunStatus = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/run-status`)
}

/**
 * 获取模拟运行详细状态（包含最近动作）
 * @param {string} simulationId
 */
export const getRunStatusDetail = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/run-status/detail`)
}

/**
 * 获取模拟中的帖子
 * @param {string} simulationId
 * @param {string} platform - 'reddit' | 'twitter'
 * @param {number} limit - 返回数量
 * @param {number} offset - 偏移量
 */
export const getSimulationPosts = (simulationId, platform = 'reddit', limit = 50, offset = 0) => {
  return service.get(`/api/simulation/${simulationId}/posts`, {
    params: { platform, limit, offset }
  })
}

/**
 * 获取模拟时间线（按轮次汇总）
 * @param {string} simulationId
 * @param {number} startRound - 起始轮次
 * @param {number} endRound - 结束轮次
 */
export const getSimulationTimeline = (simulationId, startRound = 0, endRound = null) => {
  const params = { start_round: startRound }
  if (endRound !== null) {
    params.end_round = endRound
  }
  return service.get(`/api/simulation/${simulationId}/timeline`, { params })
}

/**
 * 获取Agent统计信息
 * @param {string} simulationId
 */
export const getAgentStats = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/agent-stats`)
}

/**
 * 获取模拟动作历史
 * @param {string} simulationId
 * @param {Object} params - { limit, offset, platform, agent_id, round_num }
 */
export const getSimulationActions = (simulationId, params = {}) => {
  return service.get(`/api/simulation/${simulationId}/actions`, { params })
}

/**
 * 关闭模拟环境（优雅退出）
 * @param {Object} data - { simulation_id, timeout? }
 */
export const closeSimulationEnv = (data) => {
  return service.post('/api/simulation/close-env', data)
}

/**
 * 获取模拟环境状态
 * @param {Object} data - { simulation_id }
 */
export const getEnvStatus = (data) => {
  return service.post('/api/simulation/env-status', data)
}

/**
 * 批量采访 Agent
 * @param {Object} data - { simulation_id, interviews: [{ agent_id, prompt }] }
 */
export const interviewAgents = (data) => {
  return requestWithRetry(() => service.post('/api/simulation/interview/batch', data), 3, 1000)
}

/**
 * 获取历史模拟列表（带项目详情）
 * 用于首页历史项目展示
 * @param {number} limit - 返回数量限制
 */
export const getSimulationHistory = (limit = 20) => {
  return service.get('/api/simulation/history', { params: { limit } })
}

// =============================================================================
// Real-people cast & groups API
// =============================================================================

/**
 * Generate stakeholder groups for a simulation (LLM-proposed, user-editable)
 * @param {string} simulationId
 * @param {string} eventDescription - topic / event description
 * @param {boolean} useNamedEntities - also extract entities from Zep graph
 */
export const generateGroups = (simulationId, eventDescription, useNamedEntities = true) => {
  return requestWithRetry(
    () => service.post(`/api/simulation/${simulationId}/groups/generate`, {
      event_description: eventDescription,
      use_named_entities: useNamedEntities,
    }),
    3, 1000
  )
}

/**
 * Get current groups list for a simulation
 * @param {string} simulationId
 */
export const getGroups = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/groups`)
}

/**
 * Populate a group via Nyne search or direct LinkedIn URLs
 * @param {string} simulationId
 * @param {string} groupId
 * @param {string} method - 'nyne_search' | 'urls'
 * @param {Object} data - { urls?: string[], event_context?: string }
 */
export const populateGroup = (simulationId, groupId, method, data = {}) => {
  return service.post(`/api/simulation/${simulationId}/groups/populate`, {
    group_id: groupId,
    method,
    ...data,
  })
}

/**
 * Populate a group via CSV file upload
 * @param {string} simulationId
 * @param {string} groupId
 * @param {File} file - CSV file with a LinkedIn URL column
 */
export const uploadGroupCSV = (simulationId, groupId, file) => {
  const formData = new FormData()
  formData.append('group_id', groupId)
  formData.append('file', file)
  return service.post(`/api/simulation/${simulationId}/groups/upload-csv`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/**
 * Get real-time groups + enrichment progress (poll this during enrichment)
 * @param {string} simulationId
 */
export const getGroupsStatus = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/groups/status`)
}

/**
 * Approve the cast and trigger the full enrichment + persona pipeline
 * @param {string} simulationId
 * @param {string} simulationRequirement - topic / event description
 * @param {string} documentText - original document text (optional)
 */
export const approveGroups = (simulationId, simulationRequirement, documentText = '') => {
  return service.post(`/api/simulation/${simulationId}/groups/approve`, {
    simulation_requirement: simulationRequirement,
    document_text: documentText,
  })
}

/**
 * Update a group's name, criteria, or target_count
 * @param {string} simulationId
 * @param {string} groupId
 * @param {Object} updates - { name?, criteria?, target_count? }
 */
export const updateGroup = (simulationId, groupId, updates) => {
  return service.patch(`/api/simulation/${simulationId}/groups/${groupId}`, updates)
}

/**
 * Delete a group from the cast
 * @param {string} simulationId
 * @param {string} groupId
 */
export const deleteGroup = (simulationId, groupId) => {
  return service.delete(`/api/simulation/${simulationId}/groups/${groupId}`)
}

/**
 * Get the grounding report (available after prepare completes)
 * @param {string} simulationId
 */
export const getGroundingReport = (simulationId) => {
  return service.get(`/api/simulation/${simulationId}/grounding-report`)
}

