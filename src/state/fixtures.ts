/**
 * Synthetic local fixtures. No production accounts or real business data:
 * two resources with deliberately overlapping representations so that
 * wildcards, q weights, parameter matching and language fallback all
 * have something to bite on.
 */

export interface FixtureRepresentation {
  id: string;
  mediaType: string;
  mediaParams: Record<string, string>;
  language: string;
  body: string;
}

export interface FixtureResource {
  id: string;
  path: string;
  title: string;
  representations: FixtureRepresentation[];
}

export const FIXTURES: FixtureResource[] = [
  {
    id: 'welcome',
    path: '/welcome',
    title: 'Welcome notice',
    representations: [
      {
        id: 'welcome-html-en',
        mediaType: 'text/html',
        mediaParams: {},
        language: 'en',
        body: '<!doctype html><html lang="en"><body><h1>Welcome</h1><p>This service negotiates content.</p></body></html>',
      },
      {
        id: 'welcome-html-zh',
        mediaType: 'text/html',
        mediaParams: {},
        language: 'zh-cn',
        body: '<!doctype html><html lang="zh-CN"><body><h1>欢迎</h1><p>本服务进行内容协商。</p></body></html>',
      },
      {
        id: 'welcome-json-en',
        mediaType: 'application/json',
        mediaParams: {},
        language: 'en',
        body: JSON.stringify({ title: 'Welcome', text: 'This service negotiates content.' }, null, 2),
      },
      {
        id: 'welcome-json-zh',
        mediaType: 'application/json',
        mediaParams: {},
        language: 'zh-cn',
        body: JSON.stringify({ title: '欢迎', text: '本服务进行内容协商。' }, null, 2),
      },
      {
        id: 'welcome-plain-en',
        mediaType: 'text/plain',
        mediaParams: { charset: 'utf-8' },
        language: 'en',
        body: 'Welcome — this service negotiates content.',
      },
    ],
  },
  {
    id: 'report',
    path: '/report',
    title: 'Quarterly report',
    representations: [
      {
        id: 'report-json-en',
        mediaType: 'application/json',
        mediaParams: {},
        language: 'en',
        body: JSON.stringify({ report: 'Q3', status: 'final' }, null, 2),
      },
      {
        id: 'report-json-fr',
        mediaType: 'application/json',
        mediaParams: {},
        language: 'fr',
        body: JSON.stringify({ rapport: 'T3', statut: 'final' }, null, 2),
      },
      {
        id: 'report-csv-en',
        mediaType: 'application/csv',
        mediaParams: {},
        language: 'en',
        body: 'quarter,status\nQ3,final\n',
      },
    ],
  },
];
