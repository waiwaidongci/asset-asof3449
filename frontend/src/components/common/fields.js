import {el} from '../../utils/dom.js';

export function field(config, current = config.default) {
  const id = `field-${config.key}`;
  const attrs = {id, name:config.key, required:config.required !== false, 'aria-describedby': config.help ? `${id}-help` : undefined};
  const options = config.type === 'bool' ? [['true', '是'], ['false', '否']]
    : config.options?.map(option => [String(option), String(option)]);
  const type = {int:'number', number:'number', date:'date', string:'text'}[config.type] || config.type;
  const input = options
    ? el('select', attrs, options.map(([value, label]) => el('option', {value, selected:value === String(current)}, label)))
    : el('input', {...attrs, type, step:config.type === 'int' ? '1' : config.type === 'number' ? 'any' : undefined,
      min: config.min, max:config.max, maxlength:config.max_length, value:current, autocomplete:config.autocomplete});
  return el('label', {for:id, class:'field'}, el('span', {}, config.label), input,
    config.help && el('small', {id:`${id}-help`}, config.help));
}

export function datasetField(datasets, selected) {
  return el('label', {for:'dataset-id', class:'field'}, el('span', {}, '数据批次'),
    el('select', {id:'dataset-id', name:'dataset_id', required:true}, datasets.map(item =>
      el('option', {value:item.id, selected:String(item.id) === String(selected)}, `${item.name} · ${item.row_count} 行`))));
}

export function relabelField(sheets, selected) {
  return el('label', {for:'relabel-sheet-id', class:'field'}, el('span', {}, '换标单版本（可选）'),
    el('select', {id:'relabel-sheet-id', name:'relabel_sheet_id'},
      el('option', {value:'', selected:selected == null}, '不交换标单，按原始编号盘点'),
      sheets.map(sheet => el('option', {value:sheet.id, selected:String(sheet.id) === String(selected)},
        `${sheet.name} · 第 ${sheet.version} 版 · ${sheet.row_count} 行 · ${sheet.checksum.slice(0, 8)}`))),
    el('small', {}, '运行从排队到出账都认发起时固定的版本；补正换标单会形成新版本，已排队的运行不受影响。'));
}

export const feedback = () => el('p', {class:'feedback', 'aria-live':'polite', 'data-testid':'feedback'});
