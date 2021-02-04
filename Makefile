build: clean format test run

format:
	autopep8 --in-place --recursive jitsdp setup.py

test: 
	pytest tests

run:
	jitsdp borb --model ihf --start 2000 --end 2050 --experiment-name dev
	jitsdp borb --model lr --start 2000 --end 2050 --experiment-name dev
	jitsdp borb --model nb --start 2000 --end 2050 --experiment-name dev
	jitsdp borb --model mlp --start 2000 --end 2050 --experiment-name dev
	jitsdp borb --model irf --start 2000 --end 2050 --experiment-name dev
	jitsdp orb --model lr --start 2000 --end 2050 --experiment-name dev
	jitsdp orb --model mlp --start 2000 --end 2050 --experiment-name dev
	jitsdp orb --model nb --start 2000 --end 2050 --experiment-name dev
	jitsdp orb --model oht --start 2000 --end 2050 --experiment-name dev
	jitsdp borb --model ihf --start 300 --end 350 --experiment-name dev --cross-project 1 --dataset jgroups
	jitsdp borb --model lr --start 300 --end 350 --experiment-name dev --cross-project 1 --dataset jgroups
	jitsdp borb --model nb --start 300 --end 350 --experiment-name dev --cross-project 1 --dataset jgroups
	jitsdp borb --model mlp --start 300 --end 350 --experiment-name dev --cross-project 1 --dataset jgroups
	jitsdp borb --model irf --start 300 --end 350 --experiment-name dev --cross-project 1 --dataset jgroups
	jitsdp orb --model lr --start 300 --end 350 --experiment-name dev --cross-project 1 --dataset jgroups
	jitsdp orb --model mlp --start 300 --end 350 --experiment-name dev --cross-project 1 --dataset jgroups
	jitsdp orb --model nb --start 300 --end 350 --experiment-name dev --cross-project 1 --dataset jgroups
	jitsdp orb --model oht --start 300 --end 350 --experiment-name dev --cross-project 1 --dataset jgroups
	jitsdp tuning --start 0 --end 1 --cross-project 0 1 --orb-model mlp --borb-model --validation-end 5000 1000
	jitsdp testing --start 0 --end 1 --cross-project 0 1 --orb-model mlp --borb-model --testing-start 5000 --tuning-experiment-name dev --no-validation
	jitsdp report --start 0 --end 1 --cross-project 0 1 --orb-model mlp --borb-model --tuning-experiment-name dev --testing-experiment-name dev --no-validation

clean:
	rm -rf models/ logs/ tests/logs
	rm -rf data/joblib/jitsdp/data/load_runs/3d2a770fa5e4bb259129c123784a30b9/
