import React from 'react';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Heading from '@theme/Heading';
import BrainIcon from './icons/BrainIcon';
import UsersIcon from './icons/UsersIcon';
import DatabaseIcon from './icons/DatabaseIcon';
import styles from './styles.module.css';

const features = [
  {
    icon: BrainIcon,
    key: 'intelligent',
    details: [
      'detail1',
      'detail2',
      'detail3',
    ],
    color: 'from-blue-500 to-blue-600',
  },
  {
    icon: UsersIcon,
    key: 'multiAgent',
    details: [
      'detail1',
      'detail2',
      'detail3',
      'detail4',
    ],
    color: 'from-purple-500 to-purple-600',
  },
  {
    icon: DatabaseIcon,
    key: 'storage',
    details: [
      'detail1',
      'detail2',
      'detail3',
    ],
    color: 'from-green-500 to-green-600',
  },
];

const translations: Record<string, Record<string, string>> = {
  en: {
    'features.title': 'Core Features',
    'features.subtitle': 'Complete intelligent memory management solution for AI applications',
    'feature.intelligent.title': 'Intelligent Memory',
    'feature.intelligent.en': 'Intelligent Memory Management',
    'feature.intelligent.desc': 'Smart memory optimization based on Ebbinghaus forgetting curve, automatic importance scoring and context-aware retrieval',
    'feature.intelligent.detail1': 'Ebbinghaus Forgetting Curve Algorithm',
    'feature.intelligent.detail2': 'Automatic Importance Scoring',
    'feature.intelligent.detail3': 'Intelligent Memory Retrieval',
    'feature.multiAgent.title': 'Multi-Agent Support',
    'feature.multiAgent.en': 'Multi-Agent Support',
    'feature.multiAgent.desc': 'Independent agent memory spaces, cross-agent collaboration, permission control and privacy protection',
    'feature.multiAgent.detail1': 'Agent Isolation',
    'feature.multiAgent.detail2': 'Cross-Agent Collaboration',
    'feature.multiAgent.detail3': 'Permission Control',
    'feature.multiAgent.detail4': 'Privacy Protection',
    'feature.storage.title': 'Storage Backends',
    'feature.storage.en': 'Storage Backends',
    'feature.storage.desc': 'Support for OceanBase, PostgreSQL and other enterprise-grade storage with extensible architecture',
    'feature.storage.detail1': 'OceanBase (Default)',
    'feature.storage.detail2': 'PostgreSQL',
    'feature.storage.detail3': 'Extensible Architecture',
  },
  zh: {
    'features.title': '核心特性',
    'features.subtitle': '为 AI 应用提供完整的智能内存管理解决方案',
    'feature.intelligent.title': '智能内存管理',
    'feature.intelligent.en': 'Intelligent Memory Management',
    'feature.intelligent.desc': '基于艾宾浩斯遗忘曲线的智能记忆优化，自动重要性评分和上下文感知检索',
    'feature.intelligent.detail1': '艾宾浩斯遗忘曲线算法',
    'feature.intelligent.detail2': '自动重要性评分',
    'feature.intelligent.detail3': '智能记忆检索',
    'feature.multiAgent.title': '多Agent支持',
    'feature.multiAgent.en': 'Multi-Agent Support',
    'feature.multiAgent.desc': '独立的代理记忆空间，支持跨代理协作、权限控制和隐私保护',
    'feature.multiAgent.detail1': '代理隔离',
    'feature.multiAgent.detail2': '跨代理协作',
    'feature.multiAgent.detail3': '权限控制',
    'feature.multiAgent.detail4': '隐私保护',
    'feature.storage.title': '多种存储后端',
    'feature.storage.en': 'Storage Backends',
    'feature.storage.desc': '支持 OceanBase、PostgreSQL 等企业级存储，可扩展架构适配各种场景',
    'feature.storage.detail1': 'OceanBase（默认）',
    'feature.storage.detail2': 'PostgreSQL',
    'feature.storage.detail3': '可扩展架构',
  },
};

export default function Features() {
  const { i18n } = useDocusaurusContext();
  const isZh = i18n.currentLocale === 'zh';
  const t = (key: string) => {
    const lang = isZh ? 'zh' : 'en';
    return translations[lang][key] || key;
  };

  return (
    <section className={styles.features}>
      <div className="container">
        <div className={styles.header}>
          <Heading as="h2" className={styles.title}>
            {t('features.title')}
          </Heading>
          <p className={styles.subtitle}>
            {t('features.subtitle')}
          </p>
        </div>

        <div className={styles.grid}>
          {features.map((feature, index) => {
            const Icon = feature.icon;
            return (
              <div
                key={feature.key}
                className={`${styles.card} fade-in-delay-${index + 1}`}
              >
                <div className={`${styles.icon} ${styles[`icon-${feature.key}`]}`}>
                  <Icon className={styles.iconSvg} />
                </div>
                <Heading as="h3" className={styles.cardTitle}>
                  {t(`feature.${feature.key}.title`)}
                </Heading>
                <p className={styles.cardEn}>{t(`feature.${feature.key}.en`)}</p>
                <p className={styles.cardDesc}>
                  {t(`feature.${feature.key}.desc`)}
                </p>
                <ul className={styles.details}>
                  {feature.details.map((detailKey) => (
                    <li key={detailKey}>
                      <span className={styles.bullet}>•</span>
                      {t(`feature.${feature.key}.${detailKey}`)}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
