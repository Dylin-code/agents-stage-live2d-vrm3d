import type { SystemSettings } from '../../types/message'
import { getDefaultBridgeWsUrl, getDefaultServerUrl } from '../../utils/serverUrl'

export function buildDefaultSystemSettings(): SystemSettings {
  return {
    serverUrl: getDefaultServerUrl(),
    sessionStage: {
      bridgeUrl: getDefaultBridgeWsUrl(),
      modelPaths: 'assets/models/Senko_Normals/senko.model3.json',
    },
    backgroundPath: 'assets/background.jpg',
    assistantSettings: {
      assistantName: 'Senko',
      sysPrompt: undefined,
      model: 'qwen2.5',
      apiKey: undefined,
      baseUrl: undefined,
      mcpServers: '',
      agents: '',
    },
    live2DSettings: {
      modelPath: 'assets/models/Senko_Normals/senko.model3.json',
      offsetX: 0,
      offsetY: 0,
      scale: 0.5,
      themeColor: 'rgba(255, 255, 255, 0.8)',
    },
  }
}
