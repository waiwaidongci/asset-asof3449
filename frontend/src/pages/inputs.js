import {feedback, field} from '../components/common/fields.js';
import {empty, panel} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {handler} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, link, p} from '../utils/dom.js';
import {date, value} from '../utils/format.js';

function uploadForm(key, title, hint, name, template, submitText, error, upload) {
  const submit = button(submitText, {type:'submit', class:'primary'});
  const sample = button('下载示例 CSV', {class:'quiet',
    onClick:event=>handler(sample, error, () => workspace.download(template.path, template.name))(event)});
  return panel(title, p(hint, 'subtle'), sample, session.canWrite && el('form', {class:'fields',
    onSubmit:handler(submit, error, async event => {
      const data = new FormData(event.target);
      const file = data.get('source');
      if (!file?.size) throw new Error('请选择一个 CSV 文件');
      if (file.size > 2_000_000) throw new Error('CSV 最多 2 MB');
      await upload(data.get(`${key}_batch_name`) || name, file);
    })}, field({key:`${key}_batch_name`, label:'批次名称', type:'string', max_length:120}),
    el('label',{class:'field', for:`${key}-source`}, el('span',{},'业务 CSV'),
      el('input',{id:`${key}-source`, type:'file', name:'source', accept:'.csv,text/csv', required:true})), submit));
}

export async function inputs(route, refresh) {
  const {spec,datasets,relabels} = workspace;
  const selected = datasets.find(item => item.id === route.id) || datasets[0];
  const error = feedback();
  const detail = selected && await workspace.getDataset(selected.id,route.offset);
  const columns = spec.fields.map(field => ({key:field.key,label:field.label}));
  const preview = detail ? panel('批次明细', el('div',{class:'actions'},
    el('label',{for:'dataset-id',class:'sr'}, '选择数据批次'),
    el('select',{id:'dataset-id','aria-label':'选择数据批次',onChange:event=>{location.hash=`#inputs/${event.target.value}`;}},
      datasets.map(item=>el('option',{value:item.id,selected:item.id === selected.id}, item.name))),
    link('使用此批次运行',`#run/${detail.id}`,{class:'quiet'})),
    p(`${detail.filename} · ${detail.row_count} 行 · ${date(detail.created_at)}`,'filename'),
    table(columns,detail.rows,{caption:'批次明细',pageSize:16}),
    el('div',{class:'pagination'},
      route.offset > 0 && link('上一批明细',`#inputs/${detail.id}/${Math.max(0,route.offset-16)}`,{class:'quiet'}),
      p(`${detail.rows.length ? route.offset+1 : route.offset}–${route.offset+detail.rows.length} / ${detail.row_count}`),
      route.offset+16 < detail.row_count && link('下一批明细',`#inputs/${detail.id}/${route.offset+16}`,{class:'quiet'})))
    : empty('没有可用的批次。请导入示例或业务 CSV。');

  const relabelColumns = spec.relabel.fields.map(item => ({key:item.key,label:item.label}));
  const latestRelabel = relabels.length ? await workspace.getRelabel(relabels[0].id) : null;
  const relabelPanel = panel('换标单版本', p(spec.relabel.help, 'subtle'),
    table([{key:'name',label:'换标单'},{key:'version',label:'版本'},
      {label:'导入时间',render:row=>date(row.created_at)},
      {key:'row_count',label:'行数'},
      {label:'校验指纹',render:row=>value(row.checksum.slice(0, 12))}],
      relabels, {caption:'换标单版本', empty:'还没有导入换标单；未使用换标单的批次照常盘点。'}),
    latestRelabel && el('details', {class:'relabel-preview'},
      el('summary', {}, `查看最新版本明细（${latestRelabel.name} 第 ${latestRelabel.version} 版）`),
      table(relabelColumns, latestRelabel.rows, {caption:'最新换标单明细', pageSize:8})),
    uploadForm('relabel', '换标单导入', '同名换标单补正后形成新版本；已排队和已完成的盘点仍按发起时的版本认账。',
      spec.relabel.default_dataset, {path:'/relabels/template', name:spec.relabel.sample_name}, '校验并导入换标单',
      error, async (name, file) => {
        const item = await workspace.uploadRelabel({name, filename:file.name, csv_text:await file.text()});
        await refresh();
        error.textContent = item.created ? `已导入 ${item.name} 第 ${item.version} 版。` : '该版本已存在，未重复导入。';
      }));

  const dictionary = (title, fields) => panel(title, el('dl', {class:'dictionary'}, fields.map(item =>
    el('div', {}, el('dt', {}, item.label),
      el('dd', {}, item.key, item.required === false ? ' · 可空' : ' · 必填',
        item.options ? ` · ${item.options.join(' / ')}` : '')))));

  return el('div', {class:'two-col'}, el('div', {class:'wide-panel'}, preview, relabelPanel),
    el('aside', {class:'sidebar-panel'},
      uploadForm('dataset', '交接事件导入', '导入后保留原批次和校验指纹，可用同一份数据复跑。',
        spec.default_dataset, {path:'/template', name:spec.sample_name}, '校验并导入', error,
        async (name, file) => {
          const item = await workspace.upload({name, filename:file.name, csv_text:await file.text()});
          const target = `#inputs/${item.id}`;
          if (location.hash === target) await refresh();
          else location.hash = target;
        }),
      error,
      dictionary('交接事件字段', spec.fields),
      dictionary('换标单字段', spec.relabel.fields)));
}
