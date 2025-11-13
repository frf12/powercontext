import React, { useState } from 'react';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Heading from '@theme/Heading';
import SpeedIcon from './icons/SpeedIcon';
import SaveIcon from './icons/SaveIcon';
import SmartIcon from './icons/SmartIcon';
import styles from './styles.module.css';

// Order: smart, speed, save
const valueProps = [
  { icon: SmartIcon, key: 'smart' },
  { icon: SpeedIcon, key: 'speed' },
  { icon: SaveIcon, key: 'save' },
];

// Comparison data - labels will be translated
const getComparisonData = (isZh: boolean) => ({
  smart: {
    fullContext: 52.9,
    powermem: 78.7,
    unit: '',
    label: isZh ? 'LLM 评分' : 'LLM Score',
  },
  speed: {
    fullContext: 17.12,
    powermem: 1.44,
    unit: 's',
    label: isZh ? '响应时间' : 'Response Time',
  },
  save: {
    fullContext: 26,
    powermem: 0.9,
    unit: 'k',
    label: isZh ? 'Token 使用量' : 'Token Usage',
  },
});

const translations: Record<string, Record<string, string>> = {
  en: {
    'valueProps.title': 'Why Choose PowerMem?',
    'valueProps.subtitle': 'Speed, Save, Smart - The best AI memory management experience',
    'valueProps.benchmarkDesc': 'Real-world performance metrics based on LOCOMO dataset',
    'valueProps.viewBenchmark': 'View Full Benchmark Results',
    'valueProps.speed.title': 'Speed',
    'valueProps.speed.en': 'Faster',
    'valueProps.speed.desc': 'Ultra-fast retrieval response, high-performance async processing, intelligent cache optimization',
    'valueProps.speed.feature1': 'Ultra-fast Retrieval',
    'valueProps.speed.feature2': 'High-Performance Async',
    'valueProps.speed.feature3': 'Smart Cache Optimization',
    'valueProps.save.title': 'Save',
    'valueProps.save.en': 'More Economical',
    'valueProps.save.desc': 'Reduce storage costs, intelligent memory management, efficient resource utilization',
    'valueProps.save.feature1': 'Lower Storage Costs',
    'valueProps.save.feature2': 'Smart Memory Management',
    'valueProps.save.feature3': 'Efficient Resource Usage',
    'valueProps.smart.title': 'Smart',
    'valueProps.smart.en': 'More Accurate',
    'valueProps.smart.desc': 'Precise memory retrieval, AI-driven importance scoring, context-aware matching',
    'valueProps.smart.feature1': 'Precise Memory Retrieval',
    'valueProps.smart.feature2': 'AI-Driven Scoring',
    'valueProps.smart.feature3': 'Context-Aware Matching',
  },
  zh: {
    'valueProps.title': '为什么选择 PowerMem？',
    'valueProps.subtitle': '更快、更省、更准 - 最佳的 AI 内存管理体验',
    'valueProps.benchmarkDesc': '基于 LOCOMO 数据集的真实性能指标',
    'valueProps.viewBenchmark': '查看完整压测数据',
    'valueProps.speed.title': '更快',
    'valueProps.speed.en': 'Speed',
    'valueProps.speed.desc': '极速检索响应，高性能异步处理，智能缓存优化',
    'valueProps.speed.feature1': '极速检索响应',
    'valueProps.speed.feature2': '高性能异步处理',
    'valueProps.speed.feature3': '智能缓存优化',
    'valueProps.save.title': '更省',
    'valueProps.save.en': 'Save',
    'valueProps.save.desc': '降低存储成本，智能内存管理，资源高效利用',
    'valueProps.save.feature1': '降低存储成本',
    'valueProps.save.feature2': '智能内存管理',
    'valueProps.save.feature3': '资源高效利用',
    'valueProps.smart.title': '更准',
    'valueProps.smart.en': 'Smart',
    'valueProps.smart.desc': '精准记忆检索，AI 驱动的重要性评分，上下文感知匹配',
    'valueProps.smart.feature1': '精准记忆检索',
    'valueProps.smart.feature2': 'AI 驱动评分',
    'valueProps.smart.feature3': '上下文感知匹配',
  },
};

export default function ValueProps() {
  const { i18n } = useDocusaurusContext();
  const isZh = i18n.currentLocale === 'zh';
  const [hoveredKey, setHoveredKey] = useState<string | null>(valueProps[0].key);
  const t = (key: string) => {
    const lang = isZh ? 'zh' : 'en';
    return translations[lang][key] || key;
  };

  const comparisonData = getComparisonData(isZh);
  const activeComparison = hoveredKey ? comparisonData[hoveredKey as keyof typeof comparisonData] : null;

  return (
    <section className={styles.valueProps}>
      <div className="container">
        <div className={styles.header}>
          <Heading as="h2" className={styles.title}>
            {t('valueProps.title')}
          </Heading>
          <p className={styles.subtitle}>
            {t('valueProps.subtitle')}
          </p>
          <p className={styles.benchmarkDesc}>
            {t('valueProps.benchmarkDesc')}
          </p>
        </div>

        <div className={styles.content}>
          {/* Left side: Cards */}
          <div className={styles.cardsContainer}>
            {valueProps.map((prop, index) => {
              const Icon = prop.icon;
              const isActive = hoveredKey === prop.key;
              return (
                <div
                  key={prop.key}
                  className={`${styles.card} ${isActive ? styles.cardActive : ''} fade-in-delay-${index + 1}`}
                  onMouseEnter={() => setHoveredKey(prop.key)}
                  style={{'--index': index} as React.CSSProperties}
                >
                  <div className={styles.cardContent}>
                    <div className={styles.cardHeader}>
                      <div className={styles.icon}>
                        <Icon className={styles.iconSvg} />
                      </div>
                      <div className={styles.cardTitleWrapper}>
                        <Heading as="h3" className={styles.cardTitle}>
                          {t(`valueProps.${prop.key}.title`)}
                        </Heading>
                        <p className={styles.cardEn}>{t(`valueProps.${prop.key}.en`)}</p>
                      </div>
                    </div>
                    <p className={styles.cardDesc}>
                      {t(`valueProps.${prop.key}.desc`)}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Right side: Comparison */}
          <div className={styles.comparisonContainer}>
            {activeComparison && (
              <div className={styles.comparison}>
                <div className={styles.comparisonHeader}>
                  <Heading as="h3" className={styles.comparisonTitle}>
                    {activeComparison.label}
                  </Heading>
                </div>
                <div className={styles.comparisonData}>
                  {/* PowerMem */}
                  <div className={`${styles.comparisonItem} ${styles.comparisonItemPowerMem}`}>
                    <div className={styles.comparisonLabel}>PowerMem</div>
                    <div className={styles.comparisonValue}>
                      {activeComparison.powermem}
                      <span className={styles.comparisonUnit}>{activeComparison.unit}</span>
                    </div>
                    <div className={styles.improvement}>
                      {hoveredKey === 'smart' && (
                        <span className={styles.improvementText}>
                          +{(activeComparison.powermem - activeComparison.fullContext).toFixed(1)}%
                        </span>
                      )}
                      {hoveredKey === 'speed' && (
                        <span className={styles.improvementText}>
                          {((activeComparison.fullContext / activeComparison.powermem).toFixed(1))}x faster
                        </span>
                      )}
                      {hoveredKey === 'save' && (
                        <span className={styles.improvementText}>
                          {((activeComparison.fullContext / activeComparison.powermem).toFixed(1))}x less
                        </span>
                      )}
                    </div>
                  </div>
                  
                  {/* VS */}
                  <div className={styles.vsDivider}>
                    <span className={styles.vsText}>VS</span>
                  </div>

                  {/* Full-Context */}
                  <div className={styles.comparisonItem}>
                    <div className={styles.comparisonLabel}>
                      {isZh ? 'Full-Context' : 'Full-Context'}
                    </div>
                    <div className={styles.comparisonValue}>
                      {activeComparison.fullContext}
                      <span className={styles.comparisonUnit}>{activeComparison.unit}</span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className={styles.footer}>
          <Link
            to="/benchmark"
            className="button button--secondary"
          >
            {t('valueProps.viewBenchmark')} →
          </Link>
        </div>
      </div>
    </section>
  );
}
