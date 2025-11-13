import React from 'react';
import ComponentCreator from '@docusaurus/ComponentCreator';

export default [
  {
    path: '/zh/benchmark',
    component: ComponentCreator('/zh/benchmark', '53d'),
    exact: true
  },
  {
    path: '/zh/community',
    component: ComponentCreator('/zh/community', '266'),
    exact: true
  },
  {
    path: '/zh/docs/',
    component: ComponentCreator('/zh/docs/', '6f6'),
    exact: true
  },
  {
    path: '/zh/features',
    component: ComponentCreator('/zh/features', '70e'),
    exact: true
  },
  {
    path: '/zh/icons/AnalyticsIcon',
    component: ComponentCreator('/zh/icons/AnalyticsIcon', '015'),
    exact: true
  },
  {
    path: '/zh/icons/CloudIcon',
    component: ComponentCreator('/zh/icons/CloudIcon', '01a'),
    exact: true
  },
  {
    path: '/zh/icons/NetworkIcon',
    component: ComponentCreator('/zh/icons/NetworkIcon', '743'),
    exact: true
  },
  {
    path: '/zh/icons/ShieldIcon',
    component: ComponentCreator('/zh/icons/ShieldIcon', 'efd'),
    exact: true
  },
  {
    path: '/zh/icons/SyncIcon',
    component: ComponentCreator('/zh/icons/SyncIcon', '855'),
    exact: true
  },
  {
    path: '/zh/docs',
    component: ComponentCreator('/zh/docs', '7f2'),
    routes: [
      {
        path: '/zh/docs',
        component: ComponentCreator('/zh/docs', '7bd'),
        routes: [
          {
            path: '/zh/docs',
            component: ComponentCreator('/zh/docs', 'eb4'),
            routes: [
              {
                path: '/zh/docs/api/agents',
                component: ComponentCreator('/zh/docs/api/agents', 'dd3'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/api/async_memory',
                component: ComponentCreator('/zh/docs/api/async_memory', '8d9'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/api/mcp',
                component: ComponentCreator('/zh/docs/api/mcp', '45c'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/api/memory',
                component: ComponentCreator('/zh/docs/api/memory', '42d'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/api/overview',
                component: ComponentCreator('/zh/docs/api/overview', '282'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/architecture/overview',
                component: ComponentCreator('/zh/docs/architecture/overview', '216'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/benchmark/overview',
                component: ComponentCreator('/zh/docs/benchmark/overview', 'b14'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/development/overview',
                component: ComponentCreator('/zh/docs/development/overview', '735'),
                exact: true
              },
              {
                path: '/zh/docs/examples/overview',
                component: ComponentCreator('/zh/docs/examples/overview', '6f0'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/examples/scenario_1_basic_usage',
                component: ComponentCreator('/zh/docs/examples/scenario_1_basic_usage', 'f6b'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/examples/scenario_2_intelligent_memory',
                component: ComponentCreator('/zh/docs/examples/scenario_2_intelligent_memory', '17a'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/examples/scenario_3_multi_agent',
                component: ComponentCreator('/zh/docs/examples/scenario_3_multi_agent', '791'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/examples/scenario_4_async_operations',
                component: ComponentCreator('/zh/docs/examples/scenario_4_async_operations', '594'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/examples/scenario_5_custom_integration',
                component: ComponentCreator('/zh/docs/examples/scenario_5_custom_integration', 'f56'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/examples/scenario_6_sub_stores',
                component: ComponentCreator('/zh/docs/examples/scenario_6_sub_stores', '4cb'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/examples/scenario_7_multimodal',
                component: ComponentCreator('/zh/docs/examples/scenario_7_multimodal', '6bc'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/examples/scenario_8_ebbinghaus_forgetting_curve',
                component: ComponentCreator('/zh/docs/examples/scenario_8_ebbinghaus_forgetting_curve', '45a'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/guides/configuration',
                component: ComponentCreator('/zh/docs/guides/configuration', '0b0'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/guides/custom_prompts_usage',
                component: ComponentCreator('/zh/docs/guides/custom_prompts_usage', '001'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/guides/ebbinghaus_forgetting_curve',
                component: ComponentCreator('/zh/docs/guides/ebbinghaus_forgetting_curve', '45a'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/guides/getting_started',
                component: ComponentCreator('/zh/docs/guides/getting_started', '47c'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/guides/getting_started_async',
                component: ComponentCreator('/zh/docs/guides/getting_started_async', '355'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/guides/integrations',
                component: ComponentCreator('/zh/docs/guides/integrations', '0e6'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/guides/multi_agent',
                component: ComponentCreator('/zh/docs/guides/multi_agent', 'd63'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/guides/multimodal',
                component: ComponentCreator('/zh/docs/guides/multimodal', '432'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/guides/overview',
                component: ComponentCreator('/zh/docs/guides/overview', '8c4'),
                exact: true,
                sidebar: "docsSidebar"
              },
              {
                path: '/zh/docs/guides/sub_stores',
                component: ComponentCreator('/zh/docs/guides/sub_stores', 'e53'),
                exact: true,
                sidebar: "docsSidebar"
              }
            ]
          }
        ]
      }
    ]
  },
  {
    path: '/zh/',
    component: ComponentCreator('/zh/', 'a79'),
    exact: true
  },
  {
    path: '*',
    component: ComponentCreator('*'),
  },
];
