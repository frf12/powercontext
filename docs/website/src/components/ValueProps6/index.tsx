import React, { useState } from 'react';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Heading from '@theme/Heading';
import SpeedIcon from '../ValueProps/icons/SpeedIcon';
import SaveIcon from '../ValueProps/icons/SaveIcon';
import SmartIcon from '../ValueProps/icons/SmartIcon';
import styles from './styles.module.css';

const valueProps = [
  { icon: SmartIcon, key: 'smart' },
  { icon: SpeedIcon, key: 'speed' },
  { icon: SaveIcon, key: 'save' },
];

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
    'valueProps.title': 'Why Choose PowerMem?-6',
    'valueProps.subtitle': 'Speed, Save, Smart - The best AI memory management experience',
    'valueProps.benchmarkDesc': 'Real-world performance metrics based on LOCOMO dataset',
    'valueProps.viewBenchmark': 'View Full Benchmark Results',
    'valueProps.speed.title': 'Speed',
    'valueProps.speed.en': 'Faster',
    'valueProps.speed.desc': 'Ultra-fast retrieval response, high-performance async processing, intelligent cache optimization',
    'valueProps.save.title': 'Save',
    'valueProps.save.en': 'More Economical',
    'valueProps.save.desc': 'Reduce storage costs, intelligent memory management, efficient resource utilization',
    'valueProps.smart.title': 'Smart',
    'valueProps.smart.en': 'More Accurate',
    'valueProps.smart.desc': 'Precise memory retrieval, AI-driven importance scoring, context-aware matching',
  },
  zh: {
    'valueProps.title': '为什么选择 PowerMem？-6',
    'valueProps.subtitle': '更快、更省、更准 - 最佳的 AI 内存管理体验',
    'valueProps.benchmarkDesc': '基于 LOCOMO 数据集的真实性能指标',
    'valueProps.viewBenchmark': '查看完整压测数据',
    'valueProps.speed.title': '更快',
    'valueProps.speed.en': 'Speed',
    'valueProps.speed.desc': '极速检索响应，高性能异步处理，智能缓存优化',
    'valueProps.save.title': '更省',
    'valueProps.save.en': 'Save',
    'valueProps.save.desc': '降低存储成本，智能内存管理，资源高效利用',
    'valueProps.smart.title': '更准',
    'valueProps.smart.en': 'Smart',
    'valueProps.smart.desc': '精准记忆检索，AI 驱动的重要性评分，上下文感知匹配',
  },
};

export default function ValueProps6() {
  const { i18n } = useDocusaurusContext();
  const isZh = i18n.currentLocale === 'zh';
  const [hoveredKey, setHoveredKey] = useState<string | null>(valueProps[0].key);
  const t = (key: string) => {
    const lang = isZh ? 'zh' : 'en';
    return translations[lang][key] || key;
  };

  const comparisonData = getComparisonData(isZh);
  const activeComparison = hoveredKey ? comparisonData[hoveredKey as keyof typeof comparisonData] : null;

  const getBarHeight = (value: number, max: number) => {
    return Math.min((value / max) * 100, 100);
  };

  const maxValue = activeComparison ? Math.max(activeComparison.fullContext, activeComparison.powermem) * 1.2 : 100;

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
          <div className={styles.cardsContainer}>
            {valueProps.map((prop, index) => {
              const Icon = prop.icon;
              const isActive = hoveredKey === prop.key;
              return (
                <div
                  key={prop.key}
                  className={`${styles.card} ${isActive ? styles.cardActive : ''}`}
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

          <div className={styles.comparisonContainer}>
            {activeComparison && (
              <div className={styles.comparison}>
                <div className={styles.comparisonHeader}>
                  <Heading as="h3" className={styles.comparisonTitle}>
                    {activeComparison.label}
                  </Heading>
                </div>
                <div className={styles.comparisonData}>
                  {/* Vertical Bar Chart Layout */}
                  <div className={styles.barChartContainer}>
                    {/* PowerMem Bar */}
                    <div className={styles.barItem}>
                      <div className={styles.barLabel}>PowerMem</div>
                      <div className={styles.barWrapper}>
                        <div className={styles.barBackground}>
                          <div
                            className={`${styles.barFill} ${styles.barFillPowerMem}`}
                            style={{ height: `${getBarHeight(activeComparison.powermem, maxValue)}%` }}
                          >
                            <div className={styles.barValue}>
                              {activeComparison.powermem}
                              <span className={styles.barUnit}>{activeComparison.unit}</span>
                            </div>
                          </div>
                        </div>
                      </div>
                      <div className={styles.barImprovement}>
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

                    {/* VS Divider */}
                    <div className={styles.vsDivider}>
                      <span className={styles.vsText}>VS</span>
                    </div>

                    {/* Full-Context Bar */}
                    <div className={styles.barItem}>
                      <div className={styles.barLabel}>Full-Context</div>
                      <div className={styles.barWrapper}>
                        <div className={styles.barBackground}>
                          <div
                            className={`${styles.barFill} ${styles.barFillFullContext}`}
                            style={{ height: `${getBarHeight(activeComparison.fullContext, maxValue)}%` }}
                          >
                            <div className={styles.barValue}>
                              {activeComparison.fullContext}
                              <span className={styles.barUnit}>{activeComparison.unit}</span>
                            </div>
                          </div>
                        </div>
                      </div>
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

