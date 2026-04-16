"""
main_batch_pipeline.py


  # If no arguments (just the script name), launch the Tkinter GUI for batch_claude_submit.py
  if len(sys.argv) == 1:
    print("Launching GUI interface for batch submission...")
    run([sys.executable, 'batch_claude_submit.py'])
    return

  parser = argparse.ArgumentParser(description="Run full batch pipeline.")
  parser.add_argument('--seed', required=True)
  parser.add_argument('--lang', required=True)
  parser.add_argument('--version', required=True)
  parser.add_argument('--output', required=True)
  parser.add_argument('--model', default='claude-haiku-4-5-20251001')
  parser.add_argument('--repair', action='store_true')
  parser.add_argument('--validate', action='store_true')
  args = parser.parse_args()

  # 1. Submit batch
  state_file = None
  submit_cmd = [sys.executable, 'batch_claude_submit.py',
          '--seed', args.seed,
          '--lang', args.lang,
          '--version', args.version,
          '--output', args.output,
          '--model', args.model]
  run(submit_cmd)

  # Find the latest state file
  state_files = sorted(Path('.').glob('batch_state_*.json'), key=os.path.getmtime, reverse=True)
  if not state_files:
    print('No batch_state_*.json found!')
    sys.exit(1)
  state_file = str(state_files[0])
  print(f"Using state file: {state_file}")

  # 2. Collect results
  run([sys.executable, 'batch_claude_collect.py', '--state', state_file])

  # Find latest error file
  error_files = sorted(Path(args.output).glob('batch_errors_*.json'), key=os.path.getmtime, reverse=True)
  if error_files and args.repair:
    error_file = str(error_files[0])
    # Find latest partial output (raw or 279-Devocional)
    partials = list(Path(args.output).glob('raw_*.json')) + list(Path(args.output).glob('279-Devocional*.json'))
    if not partials:
      print('No partial output found for repair!')
      sys.exit(1)
    existing = str(sorted(partials, key=os.path.getmtime, reverse=True)[0])
    # Set final output path
    final_out = str(Path(args.output) / f'Devocional_year_{args.seed.split("_")[-1].replace(".json", "")}_{args.lang}_{args.version}.json')
    run([sys.executable, 'batch_repair_failed.py',
       '--state', state_file,
       '--errors', error_file,
       '--existing', existing,
       '--output', final_out])

  # 3. Validate
  if args.validate:
    # Find the most recent Devocional_year output
    outputs = list(Path(args.output).glob('Devocional_year_*.json'))
    if not outputs:
      print('No Devocional_year_*.json found for validation!')
      sys.exit(1)
    final = str(sorted(outputs, key=os.path.getmtime, reverse=True)[0])
    run([sys.executable, 'validation_helper.py', final])

if __name__ == '__main__':
  main()
    # Find latest error file
    error_files = sorted(Path(args.output).glob('batch_errors_*.json'), key=os.path.getmtime, reverse=True)
    if error_files and args.repair:
        error_file = str(error_files[0])
        # Find latest partial output (raw or 279-Devocional)
        partials = list(Path(args.output).glob('raw_*.json')) + list(Path(args.output).glob('279-Devocional*.json'))
        if not partials:
            print('No partial output found for repair!')
            sys.exit(1)
        existing = str(sorted(partials, key=os.path.getmtime, reverse=True)[0])
        # Set final output path
        final_out = str(Path(args.output) / f'Devocional_year_{args.seed.split("_")[-1].replace(".json", "")}_{args.lang}_{args.version}.json')
        run([sys.executable, 'batch_repair_failed.py',
             '--state', state_file,
             '--errors', error_file,
             '--existing', existing,
             '--output', final_out])

    # 3. Validate
    if args.validate:
        # Find the most recent Devocional_year output
        outputs = list(Path(args.output).glob('Devocional_year_*.json'))
        if not outputs:
            print('No Devocional_year_*.json found for validation!')
            sys.exit(1)
        final = str(sorted(outputs, key=os.path.getmtime, reverse=True)[0])
        run([sys.executable, 'validation_helper.py', final])

if __name__ == '__main__':
    main()
