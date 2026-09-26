# 动物园谱系与繁育协调

这是一个只使用Python标准库和SQLite的模块化项目，默认端口为`8308`。所有业务规则集中在`src/rules.py`，`app.py`只负责组装依赖和启动服务。

## 模块结构

- `app.py`：命令行参数、依赖组装、启动和信号处理。
- `src/domain.py`：角色、数据结构、领域异常和基础校验。
- `src/rules.py`：状态机、权限、领域计算、冲突和跨对象校验。
- `src/repository.py`：SQLite建表、查询、事务和乐观锁。
- `src/service.py`：用例编排、幂等处理、版本控制和审计写入。
- `src/http_api.py`：HTTP路由、请求解析和统一错误响应。
- `src/audit.py`：实体操作审计时间线。
- `static/index.html`：最小演示页面。
- `tests/`：完整流程、规则和失败场景测试。

## 初始化与启动

```bash
python3 app.py --db ./data.db --port 8308
```

服务启动时会自动建表。`--host`可修改监听地址，`--db`可指定其他SQLite文件。

## 核心对象

- `animal`：个体谱系；除`name`、`sex`外，亲缘档案字段均为可选：
  - `sire_id`/`dam_id`：父本/母本，必须引用已登记动物，父本须为雄性、母本须为雌性；
  - `birth_date`：出生日期（`YYYY-MM-DD`）；
  - `litter_no`：同胎编号，同胎个体填写相同编号。
  - 对已存在的动物用`set_parents`动作补录或更正（可只传需要变更的字段，传`null`可清除）。若新父本/母本已经是该动物的后代，会返回冲突个体并保留原关系。
- `pairing`：配对建议；`transfer`：机构和运输记录。

## 主要接口

- `GET /health`：健康检查。
- `GET /api/<kind>`：按对象类型查询，可用`?status=`过滤。
- `POST /api/<kind>`：创建对象；请求体为JSON。
- `GET /api/entities/<id>`：读取对象当前版本。
- `POST /api/entities/<id>/actions`：提交`{"action":"动作名","data":{...},"expected_version":数字}`。
- `GET /api/animals/<id>/pedigree`：查看一只动物的三代祖先树（无父母记录的动物照常返回，缺失节点为`null`）。
- `GET /api/audit`：读取审计记录。

示例：补录亲缘

```bash
curl -X POST /api/entities/<动物ID>/actions \
  -H 'X-User-Id: keeper1' -H 'X-Role: registrar' -d '
  {"action":"set_parents","data":{
    "sire_id":"<父本ID>","dam_id":"<母本ID>",
    "birth_date":"2026-03-01","litter_no":"L-01"}}'
```

请求身份通过`X-User-Id`和`X-Role`请求头传入。创建和动作的可执行角色由规则引擎控制。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 局限

谱系系数是简化亲缘规则，不替代专业谱系软件、遗传咨询或法定动物运输许可。
