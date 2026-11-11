// Only esbuild parses add-on source here. All execution occurs in isolated QuickJS.
const fs = require('node:fs');
const path = require('node:path');
const esbuild = require(process.argv[2]);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
if (Object.keys(input.files).length > 2000) throw new Error('Module file budget exceeded');
const roots = [...input.roots].sort((a,b)=>b.length-a.length);
const owner = importer => roots.find(root => importer.startsWith(root));
esbuild.build({
  stdin: {contents: input.entries.map(p => `import ${JSON.stringify(p)};`).join('\n'), resolveDir: process.cwd()},
  bundle: true, write: false, format: 'esm', platform: 'neutral', target: 'es2022',
  logLevel: 'silent', plugins: [{name:'virtual-pack-files', setup(build) {
    build.onResolve({filter:/.*/}, args => {
      if (Object.hasOwn(input.builtins, args.path)) {
        const root = owner(args.importer);
        if (root === undefined || !input.dependencies[root].includes(args.path))
          throw new Error(`Script module is not declared in this pack's manifest: ${args.path}`);
        return {path: args.path, namespace:'builtin'};
      }
      if (args.importer === '<stdin>' && input.entries.includes(args.path)) return {path:args.path,namespace:'pack'};
      let resolved;
      if (args.importer && !args.path.startsWith('.')) throw new Error(`External import unavailable: ${args.path}`);
      if (args.importer) {
        resolved = path.posix.normalize(path.posix.join(path.posix.dirname(args.importer), args.path));
        const root = owner(args.importer);
        if (root === undefined || !resolved.startsWith(root)) throw new Error('Import escapes its pack');
      } else resolved = args.path;
      if (!Object.hasOwn(input.files, resolved)) throw new Error(`Module not supplied: ${resolved}`);
      return {path: resolved, namespace:'pack'};
    });
    build.onLoad({filter:/.*/,namespace:'pack'}, args => ({contents: input.files[args.path], loader:'js'}));
    build.onLoad({filter:/.*/,namespace:'builtin'}, args => ({contents: input.builtins[args.path], loader:'js'}));
  }}]
}).then(async result => {
  // Lower async functions to generators using the observable guest Promise class.
  // Wrapping after ESM linking also supports top-level await without native loaders.
  const lowered = await esbuild.transform(`(async () => {\n${result.outputFiles[0].text}\n})();`, {target:'es2016',format:'iife',logLevel:'silent'});
  process.stdout.write(JSON.stringify({code:lowered.code,warnings:[...result.warnings,...lowered.warnings]}));
})
.catch(error => {process.stdout.write(JSON.stringify({error: error.message}));process.exitCode=1;});
