// Independent Script API model. Executed only in the isolated QuickJS worker.
(() => {
  'use strict';
  const host = globalThis.__host_rpc;
  const NativePromise=Promise, nativeThen=Promise.prototype.then;
  const promiseRecords=new Map();
  const observe=promise=>{
    if(promiseRecords.has(promise))return promise;
    if(promiseRecords.size>=4096)throw new Error('Promise allocation budget exceeded');
    const record={handled:false,settled:false,error:null};promiseRecords.set(promise,record);
    nativeThen.call(promise,()=>{record.settled=true;},error=>{record.settled=true;record.error=String(error)+'\n'+String(error?.stack||'');});
    return promise;
  };
  class ObservedPromise extends NativePromise {
    constructor(executor){super(executor);observe(this);}
    static get [Symbol.species](){return NativePromise;}
    then(fulfilled,rejected){const record=promiseRecords.get(this);if(record)record.handled=true;
      const next=nativeThen.call(this,fulfilled,rejected);Object.setPrototypeOf(next,ObservedPromise.prototype);return observe(next);}
    catch(rejected){return this.then(undefined,rejected);}
  }
  globalThis.Promise=ObservedPromise;
  const rpc = (method, ...args) => {
    const reply = JSON.parse(host(method, JSON.stringify(args)));
    if (reply.error) { const error = new Error(reply.error); error.name = reply.name || 'Error'; throw error; }
    return reply.value;
  };
  const listeners = new Map(), timers = new Map(), jobs = new Map();
  const blockComponents = new Map(), itemComponents = new Map(), forms = new Map();
  let nextId = 1, currentTick = 0;
  const eventNames = {
    before: ['chatSend','itemUse','playerBreakBlock','playerInteractWithBlock','playerPlaceBlock'],
    after: ['worldLoad','playerSpawn','chatSend','itemUse',
      'playerBreakBlock','playerPlaceBlock','playerInteractWithBlock','entitySpawn','entityHurt','entityDie','entityRemove'],
  };
  const signal = name => Object.freeze({
    subscribe(callback, options) {
      if (typeof callback !== 'function') throw new TypeError('Event callback must be a function');
      if (options !== undefined) rpc('unsupported','event-filter-options',name);
      const subscriptions = listeners.get(name) || [];
      if (subscriptions.length >= 256) throw new Error('Event subscription limit');
      subscriptions.push(callback); listeners.set(name, subscriptions); return callback;
    },
    unsubscribe(callback) { listeners.set(name, (listeners.get(name)||[]).filter(cb => cb !== callback)); }
  });
  const events = (names, prefix='') => new Proxy(Object.fromEntries(names.map(n => [n, signal(prefix+n)])), {
    get(target, name) {if (!(name in target)) rpc('unsupported','event',String(name)); return target[name];}
  });
  const emit = (name, payload) => {for (const callback of [...(listeners.get(name)||[])]) callback(payload);};
  function schedule(callback, delay=1, interval=false) {
    if (typeof callback !== 'function' || !Number.isFinite(delay)) throw new TypeError('Invalid timer');
    if (timers.size >= 256) throw new Error('Timer limit');
    const id = nextId++; delay = Math.max(1, Math.floor(delay));
    timers.set(id,{at:currentTick+delay,delay:interval?delay:0,callback}); return id;
  }
  const componentRegistry = target => Object.freeze({registerCustomComponent(id, component) {
    if (target.has(id)) throw new Error(`Custom component already registered: ${id}`);
    if (target.size >= 256) throw new Error('Custom component limit');
    rpc('register', target===blockComponents?'block':'item',id,Object.keys(component));
    target.set(id,component);
  }});
  const system = Object.freeze({
    beforeEvents: events(['startup']), afterEvents: events(['scriptEventReceive']),
    get currentTick(){return currentTick;},
    run: callback => schedule(callback), runTimeout: (callback,delay=1) => schedule(callback,delay),
    runInterval: (callback,delay=1) => schedule(callback,delay,true), clearRun:id=>timers.delete(id),
    runJob(generator) {if(!generator || typeof generator.next!=='function') throw new TypeError('Expected generator');
      if(jobs.size>=64)throw new Error('Job limit'); const id=nextId++;jobs.set(id,generator);return id;},
    clearJob:id=>jobs.delete(id),
    sendScriptEvent(id,message){emit('scriptEventReceive',{id,message,sourceType:'Server'});},
  });
  class BlockPermutation {
    constructor(id,states={}) {const value=rpc('permutation',id,states);this.type={id};this._states=value.states;this._components=value.components;}
    static resolve(id,states={}) {return new BlockPermutation(id,states);}
    getState(name){return this._states[name];}
    getAllStates(){return {...this._states};}
    withState(name,value){return new BlockPermutation(this.type.id,{...this._states,[name]:value});}
    matches(id,states={}){return id===this.type.id && Object.entries(states).every(([k,v])=>this._states[k]===v);}
    getTags(){return rpc('blockTags',this.type.id);}
    hasTag(tag){return this.getTags().includes(tag);}
    getItemStack(amount=1){return new ItemStack(this.type.id,amount);}
  }
  class Block {
    constructor(position){this.location={...position};}
    _read(){return rpc('getBlock',this.location);}
    get typeId(){return this._read().identifier;}
    get type(){return {id:this.typeId};}
    get isAir(){return this.typeId==='minecraft:air';}
    get isLiquid(){return ['minecraft:water','minecraft:flowing_water','minecraft:lava','minecraft:flowing_lava'].includes(this.typeId);}
    get isSolid(){return this._read().components['minecraft:collision_box']!==false && !this.isAir;}
    get dimension(){return new Dimension('overworld');}
    get permutation(){const b=this._read();return new BlockPermutation(b.identifier,b.states);}
    setType(id){rpc('setBlock',this.location,id,{});}
    setPermutation(p){if(!(p instanceof BlockPermutation))throw new TypeError('Expected BlockPermutation');rpc('setBlock',this.location,p.type.id,p.getAllStates());}
    getTags(){return this.permutation.getTags();}
    hasTag(tag){return this.getTags().includes(tag);}
    offset(delta){return new Block({x:this.location.x+delta.x,y:this.location.y+delta.y,z:this.location.z+delta.z});}
    above(n=1){return this.offset({x:0,y:n,z:0});} below(n=1){return this.above(-n);}
    north(n=1){return this.offset({x:0,y:0,z:-n});} south(n=1){return this.north(-n);}
    east(n=1){return this.offset({x:n,y:0,z:0});} west(n=1){return this.east(-n);}
    center(){return this.offset({x:.5,y:.5,z:.5}).location;}
    bottomCenter(){return this.offset({x:.5,y:0,z:.5}).location;}
    getComponent(id){return rpc('unsupported','block-component',id);}
  }
  class ItemStack {
    constructor(type,amount=1) {this.typeId=typeof type==='string'?type:type.id;this.amount=amount;this.nameTag=undefined;this._lore=[];this._dynamic={};rpc('itemInfo',this.typeId);}
    get type(){return {id:this.typeId};} get maxAmount(){return rpc('itemInfo',this.typeId).maxAmount;}
    get isStackable(){return this.maxAmount>1;} getLore(){return [...this._lore];} setLore(lines){this._lore=[...lines];}
    clone(){return ItemStack._from(this._json());}
    getTags(){return rpc('itemInfo',this.typeId).tags;} hasTag(tag){return this.getTags().includes(tag);}
    setDynamicProperty(k,v){if(v===undefined)delete this._dynamic[k];else this._dynamic[k]=v;}
    getDynamicProperty(k){return this._dynamic[k];} getDynamicPropertyIds(){return Object.keys(this._dynamic);}
    isStackableWith(other){return other instanceof ItemStack && JSON.stringify({...this._json(),amount:1})===JSON.stringify({...other._json(),amount:1});}
    getComponent(id) {
      id=id.replace(/^minecraft:/,''); const info=rpc('itemInfo',this.typeId);
      if(id==='durability' && info.durability){const self=this;return {typeId:'minecraft:durability',maxDurability:info.durability,
        get damage(){return self._damage||0;},set damage(value){if(!Number.isInteger(value)||value<0||value>info.durability)throw new Error('Invalid durability');self._damage=value;}};}
      if(id==='food' && info.food)return {typeId:'minecraft:food',...info.food};
      if(!['durability','food'].includes(id))rpc('unsupported','item-component',id);
      return undefined;
    }
    _json(){return {typeId:this.typeId,amount:this.amount,nameTag:this.nameTag,lore:this._lore,dynamic:this._dynamic,damage:this._damage||0};}
    static _from(data){if(!data)return undefined;const value=new ItemStack(data.typeId,data.amount);value.nameTag=data.nameTag;value._lore=data.lore||[];value._dynamic=data.dynamic||{};value._damage=data.damage||0;return value;}
  }
  class Container {
    constructor(owner){this.owner=owner;}
    get size(){return rpc('inventory','size',this.owner);}
    get emptySlotsCount(){return rpc('inventory','empty',this.owner);}
    getItem(slot){return ItemStack._from(rpc('inventory','get',this.owner,slot));}
    setItem(slot,item){rpc('inventory','set',this.owner,slot,item?item._json():null);}
    addItem(item){return ItemStack._from(rpc('inventory','add',this.owner,item._json()));}
    clearAll(){rpc('inventory','clear',this.owner);}
    swapItems(slot,otherSlot,other=this){rpc('inventory','swap',this.owner,slot,other.owner,otherSlot);}
    transferItem(slot,other){return ItemStack._from(rpc('inventory','transfer',this.owner,slot,other.owner));}
    getSlot(slot){const self=this;return {getItem:()=>self.getItem(slot),setItem:item=>self.setItem(slot,item),get hasItem(){return !!self.getItem(slot);}};}
  }
  class Entity {
    constructor(id){this.id=id;}
    _read(){return rpc('entity','read',this.id);}
    get typeId(){return this._read().identifier;} get location(){const p=this._read().position;return {x:p[0],y:p[1],z:p[2]};}
    get dimension(){return new Dimension('overworld');} get isValid(){return rpc('entity','valid',this.id);}
    get nameTag(){return this._read().nameTag||'';} set nameTag(value){rpc('entity','nameTag',this.id,value);}
    get isOnGround(){return !!this._read().on_ground;} get isSneaking(){return !!this._read().sneaking;}
    getVelocity(){const p=this._read().velocity;return {x:p[0],y:p[1],z:p[2]};}
    getRotation(){const e=this._read();return {x:e.pitch||0,y:e.yaw||0};}
    setRotation(rotation){rpc('entity','rotation',this.id,rotation);}
    getHeadLocation(){const p=this.location;return {...p,y:p.y+1.62};}
    getViewDirection(){const r=this.getRotation(),p=r.x*Math.PI/180,y=r.y*Math.PI/180;return {x:-Math.sin(y)*Math.cos(p),y:-Math.sin(p),z:Math.cos(y)*Math.cos(p)};}
    teleport(location,options={}){rpc('entity','teleport',this.id,location,options);}
    tryTeleport(location,options={}){try{this.teleport(location,options);return true;}catch{return false;}}
    applyImpulse(impulse){rpc('entity','impulse',this.id,impulse);} clearVelocity(){rpc('entity','clearVelocity',this.id);}
    applyDamage(amount,options={}){return rpc('entity','damage',this.id,amount,{cause:options.cause,damagingEntity:options.damagingEntity?.id});}
    kill(){return rpc('entity','kill',this.id);} remove(){rpc('entity','remove',this.id);}
    addTag(tag){return rpc('entity','addTag',this.id,tag);} removeTag(tag){return rpc('entity','removeTag',this.id,tag);}
    getTags(){return this._read().tags||[];} hasTag(tag){return this.getTags().includes(tag);}
    getProperty(key){return this._read().properties?.[key];} setProperty(key,value){rpc('entity','property',this.id,key,value);}
    resetProperty(key){rpc('entity','resetProperty',this.id,key);}
    triggerEvent(event){rpc('entity','event',this.id,event);}
    getDynamicProperty(key){return rpc('dynamic','get',this.id,key)??undefined;}
    getDynamicPropertyIds(){return rpc('dynamic','keys',this.id);}
    setDynamicProperty(key,value){rpc('dynamic',value===undefined?'delete':'set',this.id,key,value);}
    clearDynamicProperties(){rpc('dynamic','clear',this.id);}
    addEffect(effect,duration,options={}){return rpc('entity','effect',this.id,effect,duration,options);}
    getEffects(){return this._read().effects||[];} getEffect(type){return this.getEffects().find(e=>e.typeId===type||e.typeId==='minecraft:'+type);}
    removeEffect(type){return rpc('entity','removeEffect',this.id,type);}
    runCommand(command){return rpc('command',command,this.id);}
    getComponent(id){
      id=id.replace(/^minecraft:/,'');const self=this;
      if(id==='health')return {typeId:'minecraft:health',get currentValue(){return self._read().health;},get effectiveMax(){return self._read().max_health||20;},
        setCurrentValue:value=>rpc('entity','health',self.id,value),resetToMaxValue:()=>rpc('entity','health',self.id,self._read().max_health||20)};
      if(id==='inventory')return this.id==='player'||this._read().components?.['minecraft:inventory']?{typeId:'minecraft:inventory',container:new Container(this.id),inventorySize:rpc('inventory','size',this.id)}:undefined;
      if(id==='type_family')return {typeId:'minecraft:type_family',getTypeFamilies:()=>self._read().components?.['minecraft:type_family']?.family||[],hasTypeFamily:family=>(self._read().components?.['minecraft:type_family']?.family||[]).includes(family)};
      return rpc('unsupported','entity-component',id);
    }
    hasComponent(id){return !!this.getComponent(id);}
  }
  class Player extends Entity {
    get name(){return this._read().name||'SimulatorPlayer';}
    get selectedSlotIndex(){return this._read().selected;} set selectedSlotIndex(value){rpc('entity','selected',this.id,value);}
    sendMessage(message){rpc('message',message,this.id);}
    getGameMode(){return this._read().mode;} setGameMode(mode){rpc('entity','mode',this.id,mode);}
    get onScreenDisplay(){return {setTitle:(title,options)=>rpc('display',this.id,'title',title,options),setActionBar:text=>rpc('display',this.id,'actionbar',text)};}
    playSound(sound,options){rpc('unsupported','audio-playback',sound);}
  }
  const entity = id => id==='player'?new Player(id):new Entity(id);
  class Dimension {
    constructor(id){if(!['overworld','minecraft:overworld'].includes(id))rpc('unsupported','dimension',id);this.id='minecraft:overworld';}
    getBlock(position){return new Block(position);}
    setBlockType(position,type){new Block(position).setType(typeof type==='string'?type:type.id);}
    setBlockPermutation(position,p){new Block(position).setPermutation(p);}
    getEntities(options={}){return rpc('queryEntities',options,false).map(entity);}
    getPlayers(options={}){return rpc('queryEntities',options,true).map(entity);}
    spawnEntity(id,position){return entity(rpc('spawnEntity',id,position));}
    spawnItem(item,position){return entity(rpc('spawnItem',item._json(),position));}
    runCommand(command){return rpc('command',command,null);}
    spawnParticle(name,position,variables){rpc('unsupported','particle-simulation',name);}
    playSound(name,position,options){rpc('unsupported','audio-playback',name);}
  }
  class ScoreboardObjective {
    constructor(id){this.id=id;} get displayName(){return rpc('scoreboard','get',this.id).displayName;}
    getScore(participant){return rpc('scoreboard','score',this.id,participant?.id||participant)??undefined;}
    setScore(participant,score){rpc('scoreboard','set',this.id,participant?.id||participant,score);}
    addScore(participant,score){return rpc('scoreboard','add',this.id,participant?.id||participant,score);}
    removeParticipant(participant){return rpc('scoreboard','removeParticipant',this.id,participant?.id||participant);}
    getScores(){return rpc('scoreboard','scores',this.id);}
  }
  const scoreboard = Object.freeze({
    addObjective(id,displayName=id){rpc('scoreboard','create',id,displayName);return new ScoreboardObjective(id);},
    getObjective(id){return rpc('scoreboard','get',id)?new ScoreboardObjective(id):undefined;},
    getObjectives(){return rpc('scoreboard','list').map(id=>new ScoreboardObjective(id));},
    removeObjective(id){return rpc('scoreboard','remove',id?.id||id);},
  });
  const world = Object.freeze({
    beforeEvents:events(eventNames.before,'before:'), afterEvents:events(eventNames.after),scoreboard,
    getDimension:id=>new Dimension(id),getAllPlayers:()=>[new Player('player')],getPlayers:(options={})=>rpc('queryEntities',options,true).map(entity),
    getEntity:id=>rpc('entity','valid',id)?entity(id):undefined,
    sendMessage:message=>rpc('message',message,null),
    getDynamicProperty:key=>rpc('dynamic','get','world',key)??undefined,
    setDynamicProperty:(key,value)=>rpc('dynamic',value===undefined?'delete':'set','world',key,value),
    getDynamicPropertyIds:()=>rpc('dynamic','keys','world'),clearDynamicProperties:()=>rpc('dynamic','clear','world'),
    getTimeOfDay:()=>rpc('time','get'),setTimeOfDay:value=>rpc('time','set',value),
    getAbsoluteTime:()=>currentTick,getDay:()=>Math.floor(currentTick/24000),
  });
  class BaseForm {
    constructor(kind){this._data={kind,title:'',body:'',controls:[]};}
    title(text){this._data.title=text;return this;}
    show(player){const id=nextId++;const response=rpc('form',id,player.id,this._data);return response?Promise.resolve(response):new Promise(resolve=>forms.set(id,resolve));}
  }
  class ActionFormData extends BaseForm {
    constructor(){super('action');}body(text){this._data.body=text;return this;}
    button(text,iconPath){this._data.controls.push({type:'button',text,iconPath});return this;}
    header(text){this._data.controls.push({type:'header',text});return this;}
    label(text){this._data.controls.push({type:'label',text});return this;} divider(){this._data.controls.push({type:'divider'});return this;}
  }
  class MessageFormData extends BaseForm {
    constructor(){super('message');this._data.controls=[{type:'button',text:''},{type:'button',text:''}];}
    body(text){this._data.body=text;return this;}button1(text){this._data.controls[0].text=text;return this;}button2(text){this._data.controls[1].text=text;return this;}
  }
  class ModalFormData extends BaseForm {
    constructor(){super('modal');}submitButton(text){this._data.submit=text;return this;}
    dropdown(label,options,settings={}){this._data.controls.push({type:'dropdown',label,options,value:typeof settings==='number'?settings:settings.defaultValueIndex||0});return this;}
    toggle(label,settings={}){this._data.controls.push({type:'toggle',label,value:typeof settings==='boolean'?settings:settings.defaultValue||false});return this;}
    textField(label,placeholder,settings={}){this._data.controls.push({type:'text',label,placeholder,value:typeof settings==='string'?settings:settings.defaultValue||''});return this;}
    slider(label,min,max,settings={},oldDefault){this._data.controls.push({type:'slider',label,min,max,step:typeof settings==='number'?settings:settings.valueStep||1,value:typeof settings==='number'?oldDefault??min:settings.defaultValue??min});return this;}
    header(text){this._data.controls.push({type:'header',text});return this;}label(text){this._data.controls.push({type:'label',text});return this;}
    divider(){this._data.controls.push({type:'divider'});return this;}
  }
  globalThis.console=Object.freeze(Object.fromEntries(['log','warn','error','info','debug'].map(level=>[level,(...args)=>rpc('message',args.map(a=>typeof a==='string'?a:JSON.stringify(a)).join(' '),null)])));
  Math.random=()=>rpc('random');
  // Deterministic simulation clock. No wall-clock observation is used in replay.
  const NativeDate=Date;globalThis.Date=class extends NativeDate {constructor(...args){super(...(args.length?args:[currentTick*50]));}static now(){return currentTick*50;}};
  // Dynamic compilation bypasses the async transform and its rejection tracking.
  // Block it explicitly rather than reporting errors from such code as verified.
  const rejectCompilation=()=>rpc('unsupported','dynamic-code','eval/Function constructors are not modeled');
  for(const ctor of [Function,(async()=>{}).constructor,(function*(){}).constructor,(async function*(){}).constructor]){
    Object.defineProperty(ctor.prototype,'constructor',{value:rejectCompilation,writable:false,configurable:false});
  }
  globalThis.eval=rejectCompilation;globalThis.Function=rejectCompilation;
  const serverExports={world,system,Block,BlockPermutation,Entity,Player,Dimension,ItemStack,
    GameMode:Object.freeze({Creative:'creative',Survival:'survival',Adventure:'adventure',Spectator:'spectator'}),
    Direction:Object.freeze({Up:'Up',Down:'Down',North:'North',South:'South',East:'East',West:'West'}),
    EntityDamageCause:Object.freeze({entityAttack:'entityAttack',magic:'magic',fire:'fire',fall:'fall',void:'void',none:'none'}),
    ItemTypes:Object.freeze({get:id=>rpc('itemInfo',id)&&{id},getAll:()=>rpc('itemIds').map(id=>({id}))}),
  };
  globalThis.__bedrock_api=Object.freeze({server:Object.freeze(serverExports),ui:Object.freeze({ActionFormData,ModalFormData,MessageFormData,FormCancelationReason:{UserClosed:'UserClosed',UserBusy:'UserBusy'}})});
  globalThis.__sim_dispatch=(op,encoded)=>{
    const data=JSON.parse(encoded);
    if(op==='promiseErrors'){
      const errors=[];for(const [promise,record] of promiseRecords)if(record.settled){if(record.error&&!record.handled)errors.push(record.error.slice(0,1500));promiseRecords.delete(promise);}
      return JSON.stringify(errors);
    } else if(op==='startup'){
      emit('startup',{blockComponentRegistry:componentRegistry(blockComponents),itemComponentRegistry:componentRegistry(itemComponents)});
      emit('worldLoad',{});emit('playerSpawn',{player:new Player('player'),initialSpawn:true});
    } else if(op==='tick'){
      currentTick=data;
      for(const [id,timer] of [...timers])if(timers.has(id)&&timer.at<=currentTick){if(timer.delay)timer.at=currentTick+timer.delay;else timers.delete(id);timer.callback();}
      for(const [id,generator] of [...jobs])for(let n=0;n<64&&jobs.has(id);n++){if(generator.next().done){jobs.delete(id);break;}}
    } else if(op==='event'){
      const {name,payload}=data; const value={...payload};
      if(payload.player_id || name.includes('player') || name.includes('item')) value.player=new Player(payload.player_id||'player');
      if(name.includes('item'))value.source=new Player('player');
      if(name.includes('chat'))value.sender=new Player('player');
      if(payload.position)value.block=new Block({x:payload.position[0],y:payload.position[1],z:payload.position[2]});
      if(payload.entity_id)value.entity=entity(payload.entity_id);
      if(payload.hurtEntity)value.hurtEntity=entity(payload.hurtEntity);
      if(payload.deadEntity)value.deadEntity=entity(payload.deadEntity);
      if(payload.damagingEntity)value.damagingEntity=entity(payload.damagingEntity);
      if(payload.hitEntity)value.hitEntity=entity(payload.hitEntity);
      if(payload.identifier)value.itemStack=new ItemStack(payload.identifier);
      if(payload.itemStack)value.itemStack=ItemStack._from(payload.itemStack);
      if(payload.brokenPermutation)value.brokenBlockPermutation=new BlockPermutation(payload.brokenPermutation.identifier,payload.brokenPermutation.states);
      emit(name,value);return JSON.stringify({cancel:!!value.cancel});
    } else if(op==='custom'){
      const registry=data.kind==='item'?itemComponents:blockComponents,component=registry.get(data.identifier);
      if(!component){rpc('unsupported','unregistered-component',data.identifier);return;}
      const value={...data.payload,player:new Player('player'),source:new Player('player')};
      if(data.payload.position)value.block=new Block({x:data.payload.position[0],y:data.payload.position[1],z:data.payload.position[2]});
      if(data.payload.itemStack)value.itemStack=ItemStack._from(data.payload.itemStack);
      if(data.payload.entity_id)value.entity=entity(data.payload.entity_id);
      if(typeof component[data.hook]==='function')component[data.hook](value,{params:data.params||{}});
    } else if(op==='formResponse'){
      const resolve=forms.get(data.id);if(resolve){forms.delete(data.id);resolve(data.response);}
    }
    return null;
  };
})();
