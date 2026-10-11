// 驾驶舱卡片配置页（/config/cockpit）：cockpit_groups / cockpit_cards 两张配置表的可视化增删改。
// 入口：驾驶舱「系统」组「驾驶舱配置」卡（feature=cockpit_config）；写接口后端 require_hq 兜底，
// 非本部计财部进入时加载即 403（页面内联提示）。数据改动落配置表，驾驶舱下次拉取即生效。
import { useCallback, useEffect, useState } from 'react'
import { goBack } from '../hashNav'
import { Button, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Switch, Table, Tag } from 'antd'
import { ArrowLeft, Plus, RefreshCw } from 'lucide-react'
import { api } from '../api'
import { useUiStore } from '../uiShim'
import type { CockpitAdminCardView, CockpitAdminGroupView } from '@contracts/events'

// action 与约定参数键（对齐后端 _check_card 与 contracts/schemas/cockpit.json 注释）
const ACTION_OPTIONS = [
  { value: 'sso_scms', label: 'SCMS 出票跳转' },
  { value: 'sso_kb', label: '知识库出票' },
  { value: 'xuanpu_link', label: '外链跳转' },
  { value: 'xuanpu_skill', label: '玄浦技能' },
  { value: 'skill_prompt', label: '技能引导进对话' },
  { value: 'skills', label: '技能执行（跳对话预选）' },
  { value: 'modal', label: '站内弹窗' },
  { value: 'feature', label: '站内功能组件' },
]
const PARAM_KEYS: Record<string, { key: string; label: string; required?: boolean }[]> = {
  sso_scms: [{ key: 'redirect', label: '站内落地路径（redirect）', required: true }],
  sso_kb: [],
  xuanpu_link: [{ key: 'url', label: '跳转 URL（url）', required: true }],
  xuanpu_skill: [{ key: 'skill_id', label: '技能 ID（skill_id）' }, { key: 'skill_name', label: '技能名称（skill_name）' }],
  skill_prompt: [{ key: 'skill', label: '技能标识（skill）', required: true }],
  skills: [{ key: 'skill', label: '技能标识（skill）', required: true }],
  modal: [{ key: 'modal', label: '弹窗标识（modal）', required: true }],
  feature: [{ key: 'feature', label: '功能标识（feature）', required: true }],
}
const SURFACE_OPTIONS = [
  { value: 'dashboard', label: '驾驶舱（dashboard）' },
  { value: 'xin_tai', label: 'Xin台（xin_tai）' },
]
// 全部约定键集合（提交时用于剥离，保留卡片 params 里的非约定键）
const ALL_PARAM_KEYS = new Set(Object.values(PARAM_KEYS).flatMap((ks) => ks.map((k) => k.key)))

function toast(text: string, kind: 'info' | 'err' = 'info') {
  useUiStore.getState().toast(text, kind)
}

export default function CockpitConfig() {
    const [groups, setGroups] = useState<CockpitAdminGroupView[]>([])
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState('')
  // 弹窗状态：null=关闭；{editing: null}=新增
  const [groupModal, setGroupModal] = useState<{ editing: CockpitAdminGroupView | null } | null>(null)
  const [cardModal, setCardModal] = useState<{ editing: CockpitAdminCardView | null; groupKey?: string } | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    setLoadErr('')
    api<{ groups: CockpitAdminGroupView[] }>('GET', '/cockpit/admin/config', undefined, { silent: true })
      .then((r) => setGroups(r.data.groups ?? []))
      .catch((e) => setLoadErr(e?.message || '配置加载失败'))
      .finally(() => setLoading(false))
  }, [])
  useEffect(load, [load])

  const toggleGroup = async (g: CockpitAdminGroupView, enabled: boolean) => {
    try {
      await api('PATCH', `/cockpit/groups/${g.id}`, { enabled })
      toast(enabled ? '分组已启用' : '分组已停用')
      load()
    } catch { /* toast 已提示 */ }
  }
  const toggleCard = async (c: CockpitAdminCardView, enabled: boolean) => {
    try {
      await api('PATCH', `/cockpit/cards/${c.id}`, { enabled })
      toast(enabled ? '卡片已启用' : '卡片已停用')
      load()
    } catch { /* toast 已提示 */ }
  }

  return (
    <div className="min-h-screen bg-[var(--bg)] px-[clamp(20px,4vw,56px)] py-8 text-[var(--ink-100)]">
      <div className="mx-auto max-w-[1080px]">
        {/* 顶栏：返回 / 标题 / 刷新 / 新增分组 */}
        <div className="mb-6 flex items-center justify-between">
          <Space>
            <Button icon={<ArrowLeft className="h-4 w-4" />} onClick={() => goBack()}>返回</Button>
            <h1 className="m-0 text-xl font-semibold">驾驶舱卡片配置</h1>
            <span className="text-xs text-[var(--ink-30)]">cockpit_groups / cockpit_cards</span>
          </Space>
          <Space>
            <Button icon={<RefreshCw className="h-4 w-4" />} loading={loading} onClick={load}>刷新</Button>
            <Button type="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setGroupModal({ editing: null })}>
              新增分组
            </Button>
          </Space>
        </div>

        {loadErr && (
          <div className="mb-4 rounded border border-[var(--card-border)] bg-[var(--card-bg)] px-4 py-3 text-sm text-rose-500">
            {loadErr}（写操作仅限本部计财部 hq_finance）
          </div>
        )}

        {/* 分组列表：每组一张分区卡，组头可编辑/启停/删除，组体为卡片配置表 */}
        <div className="flex flex-col gap-5">
          {groups.map((g) => (
            <section key={g.key} className="rounded-xl border border-[var(--card-border)] bg-[var(--card-bg)]">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--card-border)] px-5 py-3">
                <Space size={8} wrap>
                  <span className="text-base font-semibold">{g.name}</span>
                  <Tag>{g.key}</Tag>
                  <Tag>{g.surface}</Tag>
                  <span className="text-xs text-[var(--ink-30)]">sort {g.sort}</span>
                  <Switch size="small" checked={g.enabled} onChange={(v) => toggleGroup(g, v)} />
                  {!g.enabled && <Tag color="warning">已停用（驾驶舱不显示）</Tag>}
                </Space>
                <Space>
                  <Button size="small" onClick={() => setGroupModal({ editing: g })}>编辑分组</Button>
                  <Popconfirm title="删除分组？" description="组内卡片保留，可改挂其他分组" onConfirm={() => {
                    api('DELETE', `/cockpit/groups/${g.id}`).then(() => { toast('分组已删除'); load() }).catch(() => { })
                  }}>
                    <Button size="small" danger>删除</Button>
                  </Popconfirm>
                  <Button size="small" type="primary" ghost onClick={() => setCardModal({ editing: null, groupKey: g.key })}>
                    <Plus className="mr-1 inline h-3 w-3" />加卡片
                  </Button>
                </Space>
              </div>
              <div className="px-2 py-1">
                <Table<CockpitAdminCardView>
                  size="small"
                  rowKey="id"
                  dataSource={g.cards}
                  pagination={false}
                  columns={[
                    { title: '名称', dataIndex: 'name', width: 160, render: (v: string, c) => (
                      <Space size={6}>
                        <span className={c.enabled ? '' : 'opacity-50'}>{v}</span>
                        {c.pending && <Tag color="gold">敬请期待</Tag>}
                      </Space>
                    ) },
                    { title: '标识', dataIndex: 'key', width: 180, render: (v: string) => <span className="font-mono text-xs text-[var(--ink-50)]">{v}</span> },
                    { title: '行为', dataIndex: 'action', width: 120, render: (v: string) => <Tag>{v}</Tag> },
                    { title: '参数', dataIndex: 'params', render: (p: Record<string, unknown>) => (
                      <span className="font-mono text-xs text-[var(--ink-50)]">{JSON.stringify(p ?? {})}</span>
                    ) },
                    { title: '排序', dataIndex: 'sort', width: 64 },
                    { title: '启用', dataIndex: 'enabled', width: 64, render: (_: boolean, c) => (
                      <Switch size="small" checked={c.enabled} onChange={(v) => toggleCard(c, v)} />
                    ) },
                    { title: '操作', key: 'op', width: 130, render: (_, c) => (
                      <Space size={4}>
                        <Button size="small" type="link" onClick={() => setCardModal({ editing: c })}>编辑</Button>
                        <Popconfirm title="删除卡片？" onConfirm={() => {
                          api('DELETE', `/cockpit/cards/${c.id}`).then(() => { toast('卡片已删除'); load() }).catch(() => { })
                        }}>
                          <Button size="small" type="link" danger>删除</Button>
                        </Popconfirm>
                      </Space>
                    ) },
                  ]}
                />
              </div>
            </section>
          ))}
          {!loading && groups.length === 0 && !loadErr && (
            <div className="rounded-xl border border-[var(--card-border)] bg-[var(--card-bg)] px-5 py-10 text-center text-sm text-[var(--ink-30)]">
              暂无分组，点右上角「新增分组」创建
            </div>
          )}
        </div>
      </div>

      {/* 分组编辑弹窗 */}
      {groupModal && (
        <GroupModal
          editing={groupModal.editing}
          onClose={() => setGroupModal(null)}
          onSaved={() => { setGroupModal(null); load() }}
        />
      )}
      {/* 卡片编辑弹窗 */}
      {cardModal && (
        <CardModal
          editing={cardModal.editing}
          groupKey={cardModal.groupKey}
          groups={groups}
          onClose={() => setCardModal(null)}
          onSaved={() => { setCardModal(null); load() }}
        />
      )}
    </div>
  )
}

// ---------- 分组弹窗 ----------

function GroupModal({ editing, onClose, onSaved }: {
  editing: CockpitAdminGroupView | null
  onClose: () => void
  onSaved: () => void
}) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const submit = async () => {
    const v = await form.validateFields()
    setSaving(true)
    try {
      if (editing) await api('PATCH', `/cockpit/groups/${editing.id}`, { name: v.name, sort: v.sort, enabled: v.enabled })
      else await api('POST', '/cockpit/groups', v)
      toast(editing ? '分组已保存' : '分组已创建')
      onSaved()
    } catch { /* toast 已提示 */ } finally { setSaving(false) }
  }
  return (
    <Modal
      title={editing ? `编辑分组：${editing.name}` : '新增分组'}
      open
      onCancel={onClose}
      onOk={submit}
      confirmLoading={saving}
      destroyOnHidden
      maskClosable={false}
    >
      <Form form={form} layout="vertical" className="mt-4"
        initialValues={editing
          ? { key: editing.key, name: editing.name, sort: editing.sort, enabled: editing.enabled }
          : { surface: 'dashboard', sort: 0, enabled: true }}
      >
        <Form.Item name="key" label="分组标识（key，唯一）" rules={[{ required: true, message: '必填' }]} hidden={!!editing}>
          <Input placeholder="如 dashboard_scene" />
        </Form.Item>
        <Form.Item name="surface" label="归属壳屏" hidden={!!editing}>
          <Select options={SURFACE_OPTIONS} />
        </Form.Item>
        <Form.Item name="name" label="分组名称" rules={[{ required: true, message: '必填' }]}>
          <Input placeholder="如 应用场景" />
        </Form.Item>
        <Form.Item name="sort" label="排序值（升序）">
          <InputNumber className="w-full" />
        </Form.Item>
        <Form.Item name="enabled" label="启用" valuePropName="checked">
          <Switch />
        </Form.Item>
      </Form>
    </Modal>
  )
}

// ---------- 卡片弹窗 ----------

function CardModal({ editing, groupKey, groups, onClose, onSaved }: {
  editing: CockpitAdminCardView | null
  groupKey?: string
  groups: CockpitAdminGroupView[]
  onClose: () => void
  onSaved: () => void
}) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  // 编辑时保留 params 中的非约定键（表单只编辑约定键）
  const extraParams = editing
    ? Object.fromEntries(Object.entries(editing.params ?? {}).filter(([k]) => !ALL_PARAM_KEYS.has(k)))
    : {}
  const submit = async () => {
    const v = await form.validateFields()
    const params = Object.fromEntries(
      Object.entries({ ...extraParams, ...(v.params ?? {}) }).filter(([, val]) => val !== '' && val != null),
    )
    setSaving(true)
    try {
      if (editing) {
        await api('PATCH', `/cockpit/cards/${editing.id}`, { ...v, params })
      } else {
        await api('POST', '/cockpit/cards', { ...v, params })
      }
      toast(editing ? '卡片已保存' : '卡片已创建')
      onSaved()
    } catch { /* toast 已提示 */ } finally { setSaving(false) }
  }
  return (
    <Modal
      title={editing ? `编辑卡片：${editing.name}` : '新增卡片'}
      open
      onCancel={onClose}
      onOk={submit}
      confirmLoading={saving}
      destroyOnHidden
      maskClosable={false}
    >
      <Form form={form} layout="vertical" className="mt-4"
        initialValues={editing
          ? {
            key: editing.key, group_key: editing.group_key, name: editing.name,
            description: editing.description ?? '', action: editing.action, params: { ...editing.params },
            icon: editing.icon ?? '', sort: editing.sort, pending: editing.pending, enabled: editing.enabled,
            perm_key: editing.perm_key ?? '',
          }
          : { group_key: groupKey ?? groups[0]?.key, action: 'sso_scms', sort: 0, pending: false, enabled: true }}
      >
        <Form.Item name="key" label="卡片标识（key，唯一）" rules={[{ required: true, message: '必填' }]} hidden={!!editing}>
          <Input placeholder="如 scene_brief" />
        </Form.Item>
        <Form.Item name="group_key" label="所属分组" rules={[{ required: true, message: '必选' }]}>
          <Select options={groups.map((g) => ({ value: g.key, label: `${g.name}（${g.surface}）` }))} />
        </Form.Item>
        <Form.Item name="name" label="卡片名称" rules={[{ required: true, message: '必填' }]}>
          <Input placeholder="如 信息简报" />
        </Form.Item>
        <Form.Item name="description" label="说明文字（卡面底部小字）">
          <Input placeholder="如 月度企业简报" />
        </Form.Item>
        <Form.Item name="action" label="点击行为（action）" rules={[{ required: true, message: '必选' }]}>
          <Select options={ACTION_OPTIONS} onChange={() => form.setFieldValue('params', {})} />
        </Form.Item>
        {/* 按 action 动态渲染约定参数键（sso_kb 无参数） */}
        <Form.Item noStyle shouldUpdate={(p, c) => p.action !== c.action}>
          {({ getFieldValue }) => {
            const keys = PARAM_KEYS[String(getFieldValue('action'))] ?? []
            return keys.map((k) => (
              <Form.Item key={k.key} name={['params', k.key]} label={k.label}
                rules={k.required ? [{ required: true, message: '该 action 必填' }] : []}
              >
                <Input placeholder={k.key} />
              </Form.Item>
            ))
          }}
        </Form.Item>
        <div className="grid grid-cols-2 gap-x-4">
          <Form.Item name="sort" label="排序值（组内升序）">
            <InputNumber className="w-full" />
          </Form.Item>
          <Form.Item name="icon" label="图标标识（预留）">
            <Input />
          </Form.Item>
          <Form.Item name="perm_key" label="权限资源标识（perm_key，空=不判权）">
            <Input placeholder="留空=全员可见" />
          </Form.Item>
          <Form.Item name="pending" label="占位态（敬请期待）" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </div>
      </Form>
    </Modal>
  )
}
